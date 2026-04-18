# Logging Refactor Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor logging in `backend/app/**` and `backend/runner/**` to align with `CLAUDE.md` principles while preserving behavior.

**Architecture:** Logging-only edits with strict TDD micro-cycles. Each behavior branch is implemented as RED->GREEN steps with one test at a time. Verification combines branch-specific tests and objective grep checks scoped to touched files.

**Tech Stack:** Python 3, FastAPI, pytest, pytest-asyncio, `logging`, `uv`

---

## Chunk 1A: Scheduler Logging

### Task 1: Expired lease aggregate warning fields (TDD)

**Files:**
- Modify: `backend/tests/unit/test_scheduler.py`
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write failing test `test_reconcile_expired_leases_logs_aggregate_warning_fields`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_reconcile_expired_leases_logs_aggregate_warning_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning fields (`requeued_count`, `from_status`, `to_status`, `lease_cutoff_ts`)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_reconcile_expired_leases_logs_aggregate_warning_fields -v`
Expected: PASS

### Task 2: Orphan recovery per-task fields (TDD)

**Files:**
- Modify: `backend/tests/unit/test_scheduler.py`
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write failing test `test_recover_orphaned_tasks_logs_per_task_context`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_recover_orphaned_tasks_logs_per_task_context -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal INFO fields (`task_id`, `crate_name`, `from_status`, `to_status`)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_recover_orphaned_tasks_logs_per_task_context -v`
Expected: PASS

### Task 3: Shutdown cleanup per-task fields (TDD)

**Files:**
- Modify: `backend/tests/unit/test_scheduler.py`
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write failing test `test_cleanup_remaining_tasks_logs_per_task_context`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_cleanup_remaining_tasks_logs_per_task_context -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal INFO fields (`task_id`, `crate_name`, `reason`)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_cleanup_remaining_tasks_logs_per_task_context -v`
Expected: PASS

- [ ] **Step 5: Run full scheduler tests and commit Chunk 1A**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py -v`
Expected: PASS

```bash
git add backend/tests/unit/test_scheduler.py backend/app/services/scheduler.py
git commit -m "test(logging): cover scheduler lifecycle aggregate and per-task fields"
```

## Chunk 1B: Runner-Control API Logging (`backend/app/main.py`)

### Task 4: Helper bootstrap for authenticated runner-control tests (strict TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing smoke test `test_runner_helpers_can_create_and_claim_task`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_runner_helpers_can_create_and_claim_task -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal helper set in one change (`create_runner_and_token`, `auth_headers`, `create_pending_task`, `claim_task`) and admin token fixture**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_runner_helpers_can_create_and_claim_task -v`
Expected: PASS

### Task 5: Unknown log_type warning branch (TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_log_chunk_unknown_type_logs_warning_with_fields` asserting (`request_id`, `runner_id`, `task_id`, `log_type`, `chunk_seq`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_log_chunk_unknown_type_logs_warning_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning log for unknown `log_type` branch**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_log_chunk_unknown_type_logs_warning_with_fields -v`
Expected: PASS

### Task 6: Lease mismatch warning branch on event ingest (TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_event_lease_mismatch_logs_warning_with_fields` asserting (`request_id` fallback, `runner_id`, `task_id`, `event_seq`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_lease_mismatch_logs_warning_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal request-id helper + lease mismatch warning path**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_lease_mismatch_logs_warning_with_fields -v`
Expected: PASS

### Task 7: Event-not-applied INFO branch (TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_event_not_applied_logs_info_with_fields` asserting (`request_id`, `runner_id`, `task_id`, `event_seq`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_not_applied_logs_info_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal INFO log in not-applied branch**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_not_applied_logs_info_with_fields -v`
Expected: PASS

### Task 8: Missing-task warning on event ingest branch (TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_event_missing_task_logs_warning_with_fields` asserting (`request_id`, `runner_id`, `task_id`, `event_seq`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_missing_task_logs_warning_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning log in event-missing-task branch including `runner_id` in `extra`**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_event_missing_task_logs_warning_with_fields -v`
Expected: PASS

### Task 9: Missing-task warning on log ingest branch (TDD)

**Files:**
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_log_ingest_missing_task_logs_warning_with_fields` asserting (`request_id`, `runner_id`, `task_id`, `log_type`, `chunk_seq`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_log_ingest_missing_task_logs_warning_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning log in log-missing-task branch including `runner_id` in `extra`**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/test_main.py::test_log_ingest_missing_task_logs_warning_with_fields -v`
Expected: PASS

- [ ] **Step 5: Run full `test_main.py` and commit Chunk 1B**
Run: `uv run --directory backend pytest tests/unit/test_main.py -v`
Expected: PASS

```bash
git add backend/tests/unit/test_main.py backend/app/main.py
git commit -m "feat(logging): add runner-control branch logs with request correlation"
```

## Chunk 2: Runner Worker + Executor Logging

### Task 10: Worker metrics warning context (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_worker_metrics_warning_contains_runner_id`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_metrics_warning_contains_runner_id -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning context (`runner_id`) on metrics failure**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_metrics_warning_contains_runner_id -v`
Expected: PASS

### Task 11: Worker claim/poll/heartbeat transport warning context (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_worker_claim_transport_failure_logs_warning_with_runner_id`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_claim_transport_failure_logs_warning_with_runner_id -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal warning context in claim/poll transport failure path (no per-iteration INFO loop noise)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_claim_transport_failure_logs_warning_with_runner_id -v`
Expected: PASS

- [ ] **Step 5: Write failing test `test_worker_heartbeat_transport_failure_logs_warning_with_runner_context`**
- [ ] **Step 6: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_heartbeat_transport_failure_logs_warning_with_runner_context -v`
Expected: FAIL
- [ ] **Step 7: Implement minimal warning context in heartbeat transport failure path**
- [ ] **Step 8: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_heartbeat_transport_failure_logs_warning_with_runner_context -v`
Expected: PASS

### Task 12: Worker executor exception traceback (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_worker_executor_failure_logs_traceback`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_executor_failure_logs_traceback -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal `logger.exception` with (`runner_id`, `task_id`, `crate_name`)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_executor_failure_logs_traceback -v`
Expected: PASS

- [ ] **Step 5: Run full worker tests and commit worker changes**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py -v`
Expected: PASS

```bash
git add backend/tests/unit/runner/test_worker.py backend/runner/worker.py
git commit -m "refactor(logging): add worker transport warnings and exception boundaries"
```

### Task 13: Executor lifecycle, upload decisions, exception trace, f-string removal (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_executor.py`
- Modify: `backend/runner/executor.py`
- Test: `backend/tests/unit/runner/test_executor.py`

- [ ] **Step 1: Write failing test `test_executor_logs_lifecycle_boundaries`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_logs_lifecycle_boundaries -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal lifecycle logs (`task_started`, `command_started`, `command_finished`, terminal) with context**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_logs_lifecycle_boundaries -v`
Expected: PASS

- [ ] **Step 5: Write failing test `test_executor_upload_logs_include_decisions` (`missing|empty|sent`)**
- [ ] **Step 6: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_upload_logs_include_decisions -v`
Expected: FAIL
- [ ] **Step 7: Implement minimal upload decision logs in `_upload_logs`**
- [ ] **Step 8: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_upload_logs_include_decisions -v`
Expected: PASS

- [ ] **Step 9: Write failing test `test_executor_failure_logs_traceback`**
- [ ] **Step 10: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_failure_logs_traceback -v`
Expected: FAIL
- [ ] **Step 11: Implement minimal `task_logger.exception` in execute failure branch**
- [ ] **Step 12: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py::test_executor_failure_logs_traceback -v`
Expected: PASS

- [ ] **Step 13: Run scoped f-string grep for executor**
Run: `rg 'logger\.\w+\(f"|task_logger\.\w+\(f"' backend/runner/executor.py`
Expected: no matches

- [ ] **Step 14: Run full executor tests and commit executor changes**
Run: `uv run --directory backend pytest tests/unit/runner/test_executor.py -v`
Expected: PASS

```bash
git add backend/tests/unit/runner/test_executor.py backend/runner/executor.py
git commit -m "refactor(logging): standardize executor lifecycle upload and exceptions"
```

## Chunk 3: Docker Runner + Final Verification

### Task 14: Docker command-start boundary (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_docker_runner.py`
- Modify: `backend/runner/docker_runner.py`
- Test: `backend/tests/unit/runner/test_docker_runner.py`

- [ ] **Step 1: Write failing test `test_docker_command_start_logs_info_with_workspace_context`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_command_start_logs_info_with_workspace_context -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal INFO log `container command starting` with command/workspace context**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_command_start_logs_info_with_workspace_context -v`
Expected: PASS

### Task 15: Docker cancellation warning boundary (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_docker_runner.py`
- Modify: `backend/runner/docker_runner.py`
- Test: `backend/tests/unit/runner/test_docker_runner.py`

- [ ] **Step 1: Write failing test `test_docker_cancellation_logs_warning_with_command_summary`**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_cancellation_logs_warning_with_command_summary -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal WARNING log `container execution cancelled` with command summary**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_cancellation_logs_warning_with_command_summary -v`
Expected: PASS

### Task 16: Docker timeout/non-zero/completion/exception boundaries (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_docker_runner.py`
- Modify: `backend/runner/docker_runner.py`
- Test: `backend/tests/unit/runner/test_docker_runner.py`

- [ ] **Step 1: Write failing test `test_docker_timeout_logs_error_with_fields` asserting (`command_summary`, `timeout_seconds`, `duration_ms`)**
- [ ] **Step 2: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_timeout_logs_error_with_fields -v`
Expected: FAIL
- [ ] **Step 3: Implement minimal ERROR log `container execution timed out` with (`command_summary`, `timeout_seconds`, `duration_ms`)**
- [ ] **Step 4: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_timeout_logs_error_with_fields -v`
Expected: PASS

- [ ] **Step 5: Write failing test `test_docker_nonzero_exit_logs_warning_with_fields`**
- [ ] **Step 6: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_nonzero_exit_logs_warning_with_fields -v`
Expected: FAIL
- [ ] **Step 7: Implement minimal WARNING log `container command exited non-zero` with (`exit_code`, `duration_ms`, `stdout_log`, `stderr_log`)**
- [ ] **Step 8: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_nonzero_exit_logs_warning_with_fields -v`
Expected: PASS

- [ ] **Step 9: Write failing test `test_docker_completion_logs_info_with_fields`**
- [ ] **Step 10: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_completion_logs_info_with_fields -v`
Expected: FAIL
- [ ] **Step 11: Implement minimal INFO log `container command completed` with (`exit_code`, `duration_ms`, `stdout_log`, `stderr_log`)**
- [ ] **Step 12: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_completion_logs_info_with_fields -v`
Expected: PASS

- [ ] **Step 13: Write failing test `test_docker_unexpected_error_logs_traceback`**
- [ ] **Step 14: Run RED**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_unexpected_error_logs_traceback -v`
Expected: FAIL
- [ ] **Step 15: Implement minimal `logger.exception` in unexpected error path**
- [ ] **Step 16: Run GREEN**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py::test_docker_unexpected_error_logs_traceback -v`
Expected: PASS

- [ ] **Step 17: Run full docker runner tests and commit docker changes**
Run: `uv run --directory backend pytest tests/unit/runner/test_docker_runner.py -v`
Expected: PASS

```bash
git add backend/tests/unit/runner/test_docker_runner.py backend/runner/docker_runner.py
git commit -m "refactor(logging): normalize docker runner command lifecycle and failures"
```

### Task 17: Objective final verification

**Files:**
- Verify: `backend/app/main.py`
- Verify: `backend/app/services/scheduler.py`
- Verify: `backend/runner/worker.py`
- Verify: `backend/runner/executor.py`
- Verify: `backend/runner/docker_runner.py`

- [ ] **Step 1: Run f-string logger grep scoped to touched files**
Run: `rg 'logger\.\w+\(f"|task_logger\.\w+\(f"' backend/app/main.py backend/app/services/scheduler.py backend/runner/worker.py backend/runner/executor.py backend/runner/docker_runner.py`
Expected: no matches

- [ ] **Step 2: Run branch-specific traceback tests (objective exception coverage)**
Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_executor_failure_logs_traceback tests/unit/runner/test_executor.py::test_executor_failure_logs_traceback tests/unit/runner/test_docker_runner.py::test_docker_unexpected_error_logs_traceback -v`
Expected: PASS

- [ ] **Step 3: Run lifecycle boundary tests (objective branch coverage)**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py::test_reconcile_expired_leases_logs_aggregate_warning_fields tests/unit/test_scheduler.py::test_recover_orphaned_tasks_logs_per_task_context tests/unit/test_scheduler.py::test_cleanup_remaining_tasks_logs_per_task_context tests/unit/test_main.py::test_log_chunk_unknown_type_logs_warning_with_fields tests/unit/test_main.py::test_event_lease_mismatch_logs_warning_with_fields tests/unit/test_main.py::test_event_not_applied_logs_info_with_fields tests/unit/test_main.py::test_event_missing_task_logs_warning_with_fields tests/unit/test_main.py::test_log_ingest_missing_task_logs_warning_with_fields tests/unit/runner/test_executor.py::test_executor_logs_lifecycle_boundaries tests/unit/runner/test_docker_runner.py::test_docker_command_start_logs_info_with_workspace_context tests/unit/runner/test_docker_runner.py::test_docker_timeout_logs_error_with_fields tests/unit/runner/test_docker_runner.py::test_docker_nonzero_exit_logs_warning_with_fields tests/unit/runner/test_docker_runner.py::test_docker_completion_logs_info_with_fields -v`
Expected: PASS

- [ ] **Step 4: Run targeted touched-module test files**
Run: `uv run --directory backend pytest tests/unit/test_scheduler.py tests/unit/test_main.py tests/unit/runner/test_worker.py tests/unit/runner/test_executor.py tests/unit/runner/test_docker_runner.py -v`
Expected: PASS

- [ ] **Step 5: Run full unit suite**
Run: `uv run --directory backend pytest tests/unit -v`
Expected: PASS (or only unrelated pre-existing failures)

- [ ] **Step 6: Run sensitive-value logging grep check on touched files**
Run: `rg 'Authorization|Bearer|token_hash|token_salt' backend/app/main.py backend/app/services/scheduler.py backend/runner/worker.py backend/runner/executor.py backend/runner/docker_runner.py`
Expected: no newly introduced sensitive-value logging statements

- [ ] **Step 7: Commit final verification/polish changes**

```bash
git add backend/app/main.py backend/app/services/scheduler.py backend/runner/worker.py backend/runner/executor.py backend/runner/docker_runner.py backend/tests/unit/test_main.py backend/tests/unit/test_scheduler.py backend/tests/unit/runner/test_worker.py backend/tests/unit/runner/test_executor.py backend/tests/unit/runner/test_docker_runner.py
git commit -m "chore(logging): align backend and runner logging with CLAUDE principles"
```
