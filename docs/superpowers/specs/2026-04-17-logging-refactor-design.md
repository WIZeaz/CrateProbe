# Logging Refactor Design (CLAUDE.md-aligned)

## Goal

Refactor logging in `backend/app/**` and `backend/runner/**` so runtime logs follow the principles in `CLAUDE.md`: searchable context fields, clear lifecycle boundaries, stable message templates, correct log levels, stack traces for exceptions, and no sensitive data leakage.

## Scope

### In Scope

- Logging callsite refactor in:
  - `backend/app/main.py` (runner-control API boundaries only)
  - `backend/app/services/scheduler.py`
  - `backend/runner/worker.py`
  - `backend/runner/executor.py`
  - `backend/runner/docker_runner.py` (only logging touchpoints)
- Replace f-string logger calls with parameterized logging.
- Add/normalize contextual fields (`task_id`, `runner_id`, `crate_name`, `attempt`, etc.) via `extra={...}` where relevant.
- Ensure lifecycle boundary logs are explicit and consistently worded.
- Convert broad exception logging to `logger.exception(...)` in `except` blocks.

### Out of Scope

- No database schema changes.
- No API contract changes.
- No state machine changes.
- No new logging dependencies (no structlog/JSON stack migration in this change).
- No broad formatter overhaul.

## Design Principles Applied

1. **One event per line with stable wording**
   - Keep message templates short and consistent.
2. **Searchable context on critical paths**
   - Include identifiers in `extra` and/or arguments for indexing and grepability.
3. **Correct severity semantics**
   - `INFO`: normal lifecycle transitions.
   - `WARNING`: recoverable/transient issues.
   - `ERROR`/`exception`: failures impacting task/request.
4. **No secrets in logs**
   - Never emit bearer token, token hash/salt, full auth headers.
5. **Execution observability without log bloat**
   - Log command summary, result code, and duration; keep full output in log files.

## Component Design

### 1) `backend/app/services/scheduler.py`

#### Current Issues

- Mixed f-string messages.
- Missing structured context in key transitions.

#### Changes

- Normalize all logs to parameterized templates.
- For startup reconciliation and shutdown cleanup events, include per-task context (`task_id`, `crate_name`, old/new state, reason).
- Keep lease-requeue event at `WARNING` and **aggregate level only** (no per-task IDs in this function), including `requeued_count`, cutoff timestamp, and old/new status.

#### Expected Outcomes

- Easier to trace server-side task boundary transitions and startup/shutdown recovery actions.

### 2) `backend/runner/executor.py`

#### Current Issues

- Task lifecycle logs are present but inconsistent in format and context.
- Exception path logs do not consistently preserve traceback.

#### Changes

- Standardize lifecycle events: task start, workspace prep, command start, command finish, task terminal event.
- Replace f-string logs with parameterized calls.
- Use `task_logger.exception(...)` in exception path.
- Add per-log-type upload decision/result logs in `_upload_logs` (missing/empty/uploaded).
- Include context (`task_id`, `crate_name`, `version`, `exit_code`) at critical points.

#### Expected Outcomes

- Complete and searchable runner-side execution timeline for each task.

### 3) `backend/runner/worker.py`

#### Current Issues

- Warnings exist but context and exception handling are not fully normalized.

#### Changes

- Ensure claim/poll/heartbeat/metrics loop logs include `runner_id` and task IDs when available.
- Keep transient transport failures at `WARNING`.
- Convert unexpected executor failures to `logger.exception(...)` with task/runner context.

#### Expected Outcomes

- Clear distinction between transient control-plane noise and task-impacting failures.

### 4) `backend/runner/docker_runner.py`

#### Current Issues

- Command execution observability is partial.

#### Changes

- Normalize command execution logs at these points:
  - command start (`INFO`): command summary + workspace context
  - timeout (`ERROR`/`exception`): timeout seconds + command summary
  - cancellation (`WARNING`): cancellation boundary + command summary
  - non-zero exit (`WARNING`): exit code + duration + output pointers
  - final completion (`INFO`): exit code + duration + output pointers
- Use `logger.exception(...)` where command/runtime errors are caught.
- Preserve security posture by excluding sensitive values.

#### Expected Outcomes

- Better diagnosability of container command behavior while keeping payload concise.

### 5) `backend/app/main.py` (Targeted)

#### Current Issues

- Some critical control-flow branches (lease mismatch/unknown log type) have no explicit logging.

#### Changes

- Add boundary logs for runner-control endpoints at these points:
  - unknown `log_type` branch in chunk ingest (`WARNING`)
  - lease mismatch branch (`WARNING`)
  - task event not applied (`INFO`, idempotent/duplicate event path)
  - missing task on event/log ingest (`WARNING`)
- Include context fields (`runner_id`, `task_id`, `event_seq`, `chunk_seq`, `log_type`) where relevant.
- Include `request_id` for API-path correlation IDs on important lines. Source: `X-Request-ID` header if present; otherwise generate a short per-request UUID in endpoint scope.
- Do not log auth token material.

#### Expected Outcomes

- Faster triage for distributed runner protocol mismatches.

## Data and Context Conventions

Use these keys when available:

- `task_id`
- `request_id`
- `runner_id`
- `crate_name`
- `version`
- `attempt`
- `event_seq`
- `chunk_seq`
- `log_type`
- `exit_code`
- `duration_ms`

If a value is unknown at callsite, omit it rather than inventing sentinel text.

## Error Handling Strategy

- In `except` blocks, prefer `logger.exception("...")` / `task_logger.exception("...")`.
- Keep recoverable network/control retries as `WARNING`.
- Keep task terminal failures as `ERROR` + traceback.

## Testing Strategy (TDD-oriented)

1. Add/adjust tests first (primarily unit tests) to assert key log messages and levels using `caplog`.
2. Verify RED before code changes.
3. Implement minimal logging refactor changes.
4. Verify GREEN for updated tests.
5. Run targeted backend unit tests for scheduler/worker/executor paths.

### Target Tests

- `backend/tests/unit/test_scheduler.py`
- `backend/tests/unit/runner/test_worker.py`
- `backend/tests/unit/runner/test_executor.py`
- `backend/tests/unit/test_main.py` (runner-control boundary logs)
- `backend/tests/unit/runner/test_docker_runner.py` (non-Docker path via mocks/stubs for timeout/cancel/error logging)

## Risks and Mitigations

- **Risk:** Over-logging in hot paths.
  - **Mitigation:** Keep noisy loops at existing levels, avoid per-iteration info spam unless state changes.
- **Risk:** Test brittleness due to exact message text.
  - **Mitigation:** Assert key substrings/levels/context, avoid full string lock-in when unnecessary.
- **Risk:** Behavior drift during refactor.
  - **Mitigation:** No functional branch logic changes; logging-only edits plus existing tests.

## Acceptance Criteria

- No f-string based logger calls remain in touched modules.
- Objective check: `rg 'logger\.\w+\(f"|task_logger\.\w+\(f" backend/app backend/runner` returns no matches in touched files.
- Key lifecycle boundaries are explicitly logged in:
  - scheduler: lease requeue aggregate, orphan recovery, shutdown cleanup
  - runner worker: claim loop errors, executor failure boundary
  - runner executor: task start, command start/finish, terminal event, log upload decisions
  - docker runner: command start, timeout/cancel/non-zero exit/final completion
- Exception traces are preserved with `logger.exception(...)` in these paths:
  - executor task execution exception branch
  - worker executor failure branch
  - docker runner command/runtime exception branches
- No secret-bearing fields (tokens/auth headers) are logged.
- Target unit tests pass after refactor.

## Implementation Readiness

This design is ready for implementation in the dedicated worktree branch:

- Worktree: `/home/wizeaz/exp-plat/.worktrees/logging-refactor`
- Branch: `refactor/logging-claude-principles`
