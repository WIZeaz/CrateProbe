# Spec: Runner Log + Log Viewer Redesign

**Date:** 2026-03-15
**Status:** Approved

## Problem

1. `TaskExecutor` has no per-task log of its own operations (download, extraction, Docker setup, command execution). When the runner fails before `cargo rapx` starts, only `error_message` in the DB captures the issue — no step-by-step trace is available.
2. The `LogViewer` component uses a horizontal tab layout that doesn't scale well when adding more log files.

## Goals

- Add a per-task runner log file that records TaskExecutor operations so failures can be diagnosed.
- Redesign the Logs card to a left-right split: file list on the left (bento style), file content on the right.
- All log files refresh in real time (5s polling).
- Generalize the log API endpoint so new log files can be added without backend/frontend changes.

## Backend Changes

### 1. Runner Log File

**Path:** `workspace/logs/{task_id}-runner.log`

Using task ID (not crate+version) avoids file collisions when multiple tasks exist for the same crate and version (e.g., after retries). Path is constructed from `task.id` and `config.workspace_path`.

**File open mode:** `"w"` (overwrite). On task retry, `retryTask` reuses the same task ID (resets to pending state) — the runner log is overwritten when the task re-executes, giving each retry attempt a clean log.

**Records the following events (with timestamps):**
- Task started (ID, crate name, version, execution mode)
- Crate download start / success / failure
- Extraction start / success / failure
- Docker availability check result
- Docker image pull/verify result
- Full command line being executed
- Process start (PID)
- Process exit (exit code)
- Any exception caught by the outer `try/except`

**Implementation in `TaskExecutor.execute_task`:**

1. As the **very first action** in `execute_task` (before the `if self.execution_mode == 'docker'` branch and before updating task status), construct the runner log path, create a `logging.FileHandler` writing to that path in `"w"` mode, and attach it to a logger named `f"task.{task_id}"`.
2. Wrap the entire method body in a `try/finally` block so the handler is always closed and removed from the logger, even on exception.
3. Use `INFO` for normal steps, `ERROR` for failures/exceptions.
4. Sub-methods (`_execute_with_limiter`, etc.) retrieve the same logger via `logging.getLogger(f"task.{task_id}")` — no parameter passing required.
5. The `cfg` (config) object is available on `self.config` in all sub-methods.

The log directory (`workspace/logs/`) is created at startup by `config.ensure_workspace_structure()`.

### 2. Generalized Log API Endpoint

Replace the 6 existing individual log endpoints with 2 generic ones:

```
GET /api/tasks/{id}/logs/{name}      → { "lines": [...] }    (last N lines, ?lines=1000)
GET /api/tasks/{id}/logs/{name}/raw  → plain text download
```

`{name}` is a single URL path segment (no slashes). FastAPI's default path parameter behavior enforces this, preventing path traversal.

**Path resolution** uses a dict of resolvers in `main.py`, each taking `(task, config)`:

```python
LOG_PATH_RESOLVERS = {
    "stdout":      lambda task, _cfg: Path(task.stdout_log),
    "stderr":      lambda task, _cfg: Path(task.stderr_log),
    "runner":      lambda task, cfg: cfg.workspace_path / "logs" / f"{task.id}-runner.log",
    "miri_report": lambda task, _cfg: Path(task.workspace_path) / "testgen" / "miri_report.txt",
}
```

(`_cfg` is unused for stdout/stderr/miri_report — this is intentional.)

**Error responses (unified for both endpoints):**
- `{name}` not in resolvers → 404 `"Unknown log type"`
- File not found → 404 `"Log file not found"` ← note: replaces the old `"Miri report file not found"` detail string; existing tests asserting that old string must be updated

**The 6 existing endpoints are removed.** URL shape for existing log types is identical (`/logs/stdout`, `/logs/stderr`, `/logs/miri_report`), so `LogViewer.vue` and `api.js` continue to work without changes.

**Extensibility:** Adding a new log file requires only one entry in `LOG_PATH_RESOLVERS`.

## Frontend Changes

### `LogViewer.vue` — Left-Right Layout

Replace the horizontal tab bar with a vertical file list on the left and log content on the right, within the existing `.bento-card` wrapper.

**Layout:**
```
┌─────────────────────────────────────────────────────┐
│ Logs                                    [Download]  │
├──────────────┬──────────────────────────────────────┤
│ [icon] runner│                                       │
│ [icon] stdout│  # runner — task-42                   │
│ [icon] stderr│  [INFO] Task #42 started              │
│ [icon] miri_ │  [INFO] Downloading crate...          │
│              │  [INFO] Extraction complete            │
│  ↻ 5s刷新   │  [INFO] Running cargo rapx...         │
└──────────────┴──────────────────────────────────────┘
```

**File list (left panel):**
- Width: fixed ~160px, separated from content area by a border
- Items in order: `runner`, `stdout`, `stderr`, `miri_report`
- Each item has a small icon: runner uses a distinct icon (e.g., gear/tool) to distinguish it from log file items; the remaining three use a document icon. Exact icon choice (emoji, SVG, Heroicons) is left to the implementer.
- Selected item: blue background + border, matching the existing bento card active state
- Bottom of panel: small "↻ 5s" refresh indicator

**Content area (right panel):**
- Retains existing `.log-viewer` dark terminal style (`background: #111827`, monospace font)
- `max-height: 500px`, `overflow-y: auto`

**State initialization:**

```js
const activeLog = ref('runner')   // runner is first and most useful for diagnosing failures
const logs = ref({ runner: '', stdout: '', stderr: '', miri_report: '' })
const loading = ref({ runner: false, stdout: false, stderr: false, miri_report: false })
```

**Error handling in `loadLog` catch block** (replaces current behavior, which surfaces all errors including 404 as error text):
- `err.response?.status === 404` → set `logs.value[logType] = 'No content available'` silently. This is normal for `runner` before the task starts, and for `miri_report` if not generated.
- Other errors (network, 500) → surface error text as before: `logs.value[logType] = \`Error loading log: ${error.value}\``

**Refresh and file-switch behavior** (adapted from existing `LogViewer.vue` `watch(activeTab, ...)` logic):
- A single `setInterval` (5s) polls the currently active file (`activeLog`)
- On file switch: immediately load content if not yet fetched; scroll to bottom if `autoScroll` is true
- On auto-refresh: scroll to bottom only if user was already within 50px of the bottom

### `api.js` — No Changes Required

`getLog(taskId, logType, lines)` and `downloadLog(taskId, logType)` already accept an arbitrary `logType` string. Passing `'runner'` works without modification.

## Out of Scope

- Streaming logs via WebSocket (keep HTTP polling)
- Pagination of task list
- Any changes to Dashboard, TaskList, or other views

## Testing

- Unit test: `LOG_PATH_RESOLVERS` produces correct absolute paths for all 4 known log types
- Unit test: unknown `{name}` → 404 `"Unknown log type"` on the JSON endpoint
- Unit test: unknown `{name}` → 404 `"Unknown log type"` on the `/raw` endpoint
- Unit test: runner log path uses `task.id`, not crate+version
- Integration test: `GET /api/tasks/{id}/logs/runner` returns 200 with log content after a task runs
- **Remove or update** all existing unit/integration tests for the 6 old individual log endpoints, including any test asserting `"Miri report file not found"` (now `"Log file not found"`)
- Frontend: manual verification that all 4 files load, switch, refresh, and download correctly; runner log shows `"No content available"` before task starts
