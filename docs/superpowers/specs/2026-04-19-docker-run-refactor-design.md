# Docker Runner run() Refactor Design

**Goal:** Simplify the structure of `DockerRunner.run()` by removing unnecessary nested exception handling while preserving existing behavior.

**Architecture:** Keep the `run()` method as the orchestration point, but extract cohesive responsibilities into small helper methods (prepare, start, wait, interpret result, cleanup). Maintain a single top-level `try/except/finally` in `run()` to handle cancellations, timeouts, and unexpected errors consistently with current behavior.

**Tech Stack:** Python (asyncio), Docker SDK, pytest.

---

## Context

The current `DockerRunner.run()` implementation has multiple nested `try` blocks, making cancellation, timeout, and error handling difficult to follow. The code already has well-defined behavior that must be preserved (log syncing, container cleanup, timeout handling, OOM detection, stderr error writes, ownership reconciliation).

## Design Overview

### Key Principles

- Preserve all existing behavior and log fields.
- Use small private helpers to reduce nesting in `run()`.
- Keep cancellation semantics intact (especially late-started container cleanup).
- Ensure finalization always stops log sync, syncs logs one last time, and removes containers.

### Explicit Behavior Contracts (Must Remain Unchanged)

- **Timeout:**
  - Stop container with timeout=10.
  - Log error with fields: command_summary, workspace, timeout_seconds, duration_ms.
  - Return `ExecutionResult(state=TIMEOUT, exit_code=-1, message="Execution timed out after {timeout_seconds} seconds")`.
- **Cancellation during execution:**
  - Log warning with fields: command_summary, workspace.
  - Stop container (default timeout=5), re-raise `asyncio.CancelledError` (no ExecutionResult).
- **Cancellation during startup:**
  - Add done-callback to start future; if container eventually starts, stop and remove it.
  - Re-raise `asyncio.CancelledError`.
- **OOM detection:**
  - If exit_code == 137, return `ExecutionResult(state=OOM, exit_code=137, message="Process killed by OOM killer (out of memory)")`.
- **Non-zero exit:**
  - Log warning with fields: command_summary, workspace, exit_code, duration_ms, stdout_log, stderr_log.
  - Return `ExecutionResult(state=FAILED, exit_code=exit_code, message="Process exited with code {exit_code}")`.
- **Success:**
  - Log info with fields: command_summary, workspace, exit_code=0, duration_ms, stdout_log, stderr_log.
  - Return `ExecutionResult(state=COMPLETED, exit_code=0, message="")`.
- **Unexpected errors:**
  - Log exception with fields: command_summary, workspace.
  - Write `stderr_log` with `Unexpected error: {exc}`.
  - Return `ExecutionResult(state=FAILED, exit_code=-1, message="Unexpected error: {exc}")`.

### Proposed Helper Methods

1. **Preparation**
   - `_prepare_workspace_and_logs(workspace_dir, stdout_log, stderr_log)`
   - Responsibilities:
     - Ensure workspace + log directories exist.
     - Remove existing target stdout/stderr log files.

2. **Container Configuration**
   - `_build_wrapped_command(command)`
   - `_build_run_kwargs(workspace_dir, volumes, resource_limits, wrapped_command)`
   - Responsibilities:
     - Build shell-wrapped command redirecting to `/workspace/stdout.log` and `/workspace/stderr.log`.
     - Assemble Docker run kwargs EXACTLY as today:
       - image, command, working_dir="/workspace", volumes, detach=True
       - stdout=False, stderr=False, tty=True
       - environment={"CARGO_TERM_COLOR": "always", "TERM": "xterm-256color"}
       - resource limits: mem_limit, memswap_limit, cpu_quota, cpu_period

3. **Container Startup (Cancellation Safe)**
   - `_start_container_async(loop, run_kwargs, command_summary, workspace_dir)`
   - Responsibilities:
     - Use executor to start container.
     - Handle cancellation during startup by attaching a callback that stops/removes late-started containers.

4. **Log Sync Lifecycle**
   - `_start_log_sync(source_stdout, source_stderr, target_stdout, target_stderr, stop_event)`
   - `_stop_log_sync(log_sync_task, stop_event)`
   - Responsibilities:
     - Start periodic log sync task.
     - Interval must remain `self.log_sync_interval_seconds`.
     - Stop and await it with timeout=5; on timeout, cancel and await cancellation.
     - Final sync must be executed after stopping the task.

5. **Wait for Completion**
   - `_wait_for_container_exit(container, timeout_seconds)`
   - Responsibilities:
     - Wait on container with timeout.
     - On timeout, stop container and return timeout result.
     - On cancellation, stop container and re-raise.

6. **Result Mapping**
   - `_result_from_exit_code(exit_code, duration_ms, stdout_log, stderr_log)`
   - Responsibilities:
     - Convert exit_code to `ExecutionResult` (COMPLETED / OOM / FAILED).
     - No stderr writes for non-zero exit codes (only logging + result).

7. **Finalization**
   - `_finalize_run(container, cancelled, source_stdout, source_stderr, stdout_log, stderr_log, log_sync_task, stop_sync_event, workspace_dir)`
   - Responsibilities:
     - Stop log sync, run final incremental sync, remove container.
     - Ordering must remain: stop log sync -> final incremental sync -> remove container -> (if not cancelled) ownership reconciliation.
     - Ownership reconciliation is best-effort and exceptions are ignored.

## Error Handling Semantics

- **Cancellation during startup**: attach callback to stop/remove late-started container and log "container started after cancellation; stopping late-started container" with command_summary/workspace.
- **Cancellation during execution**: log warning "container execution cancelled" with command_summary/workspace, stop container, re-raise.
- **Timeout**: stop container with timeout=10, log error "container execution timed out" with fields, return TIMEOUT result.
- **Unexpected error**: log exception "container execution failed", write to stderr log, return FAILED.
- **Always**: stop log sync task (with timeout=5), final sync, remove container, skip ownership fix if cancelled.

## Testing Strategy

- Run existing unit tests in `backend/tests/unit/runner/test_docker_runner.py`.
- No new test cases required unless refactor introduces new helper-level behavior.

## Files

- Modify: `backend/runner/docker_runner.py`
- Tests: `backend/tests/unit/runner/test_docker_runner.py`
