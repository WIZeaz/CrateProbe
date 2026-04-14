# Project.md - CrateProbe

> This document provides a comprehensive technical reference for AI models working on this codebase. It covers architecture, data flow, file-by-file descriptions, and key patterns.

## 1. What This Project Does

CrateProbe is an automated Rust crate testing platform. It:
1. Downloads Rust crates from crates.io
2. Runs `cargo rapx` (a memory safety analysis tool) against them
3. Generates test cases and POC (Proof of Concept) exploits
4. Provides a real-time Web UI to monitor progress and view results

## 2. Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.10+, FastAPI, SQLite, uvicorn |
| Frontend | Vue 3 (Composition API), Vite, Tailwind CSS v4 |
| Package mgmt | `uv` (backend), `npm` (frontend) |
| Config | TOML (`config.toml` at project root) |
| Real-time | WebSocket (FastAPI native) + HTTP polling |
| Execution | systemd-run / Python `resource` module / Docker |

## 3. Directory Structure

```
exp-plat/
├── config.toml.example          # Configuration template (bilingual CN/EN comments)
├── CLAUDE.md                    # Instructions for Claude Code
├── Project.md                   # This file
│
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory, all REST + WS endpoints
│   │   ├── config.py            # Config dataclass, loads from config.toml
│   │   ├── database.py          # SQLite ORM (raw SQL, no SQLAlchemy)
│   │   ├── models.py            # TaskStatus enum
│   │   ├── api/
│   │   │   └── websocket.py     # ConnectionManager for WS broadcast
│   │   ├── services/
│   │   │   ├── scheduler.py     # Task scheduling loop (5s interval)
│   │   │   ├── task_executor.py # Downloads crates, runs cargo rapx
│   │   │   ├── crates_api.py    # crates.io HTTP client (httpx)
│   │   │   └── system_monitor.py# CPU/memory/disk stats (psutil)
│   │   └── utils/
│   │       ├── file_utils.py    # read_last_n_lines() for log tailing
│   │       ├── resource_limit.py# systemd-run / resource module wrapper
│   │       └── docker_runner.py # Docker container execution
│   ├── tests/
│   │   ├── unit/                # Unit tests for each module
│   │   └── integration/         # API + WebSocket integration tests
│   └── pyproject.toml           # Python dependencies (uv)
│
├── frontend/
│   ├── src/
│   │   ├── App.vue              # Root layout: nav bar + router-view + footer
│   │   ├── style.css            # Global CSS: bento cards, status badges, log viewer, spinner
│   │   ├── router/index.js      # Vue Router config (5 routes)
│   │   ├── services/
│   │   │   ├── api.js           # Axios REST client (all API methods)
│   │   │   └── websocket.js     # WebSocket singleton with reconnect logic
│   │   ├── views/
│   │   │   ├── Dashboard.vue    # Stats grid + system monitor + recent tasks table
│   │   │   ├── TaskList.vue     # Sortable/filterable task table with batch operations
│   │   │   ├── TaskNew.vue      # Create task form (crate name + optional version)
│   │   │   ├── TaskBatch.vue    # Batch task creation
│   │   │   └── TaskDetail.vue   # Task header, stats, details, log viewer
│   │   └── components/
│   │       ├── LogViewer.vue    # Tabbed log display (stdout/stderr/miri_report)
│   │       ├── StatCard.vue     # Reusable stat card with icon and color
│   │       └── SystemMonitor.vue# CPU/memory/disk progress bars
│   └── vite.config.js           # Reads config.toml for proxy settings
│
└── workspace/                    # Runtime directory (created at startup)
    ├── repos/                    # Downloaded and extracted crate source
    ├── logs/                     # stdout/stderr log files per task
    └── tasks.db                  # SQLite database
```

## 4. Database Schema

Single table `tasks` in SQLite:

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment task ID |
| `crate_name` | TEXT | Crate name from crates.io |
| `version` | TEXT | Crate version |
| `workspace_path` | TEXT | Path to extracted source: `workspace/repos/{name}-{version}` |
| `stdout_log` | TEXT | Path to stdout log: `workspace/logs/{name}-{version}-stdout.log` |
| `stderr_log` | TEXT | Path to stderr log: `workspace/logs/{name}-{version}-stderr.log` |
| `status` | TEXT | One of: pending, running, completed, failed, cancelled, timeout, oom |
| `created_at` | TIMESTAMP | Task creation time |
| `started_at` | TIMESTAMP | Execution start (nullable) |
| `finished_at` | TIMESTAMP | Execution end (nullable) |
| `case_count` | INTEGER | Number of generated test case directories |
| `poc_count` | INTEGER | Number of generated POC directories |
| `pid` | INTEGER | OS process ID while running (nullable) |
| `exit_code` | INTEGER | Process exit code (nullable) |
| `error_message` | TEXT | Error description (nullable) |
| `memory_used_mb` | REAL | Memory usage (nullable, not actively populated) |

Indexes: `idx_created_at` (DESC), `idx_status`.

## 5. Task Lifecycle

```
pending ──[scheduler picks up]──> running ──> completed (exit 0)
                                         ├──> failed (non-zero exit / exception)
                                         ├──> timeout (SIGXCPU/SIGALRM or Docker timeout)
                                         ├──> oom (SIGKILL/exit 137)
                                         └──> cancelled (user-initiated)
```

### Execution Flow (`TaskExecutor.execute_task`)

1. Set status to `running`, record `started_at`
2. **Prepare workspace**: download `.crate` from crates.io, extract tar.gz, strip top-level dir
3. **Execute**: Run `cargo rapx -testgen -test-crate={name}` with resource limits
   - **systemd mode**: wraps with `systemd-run --user --scope --property=MemoryMax=...`
   - **resource mode**: uses Python `resource.setrlimit` in `preexec_fn`
   - **docker mode**: runs in container with mem/cpu limits, mounts workspace to `/workspace`
4. **Monitor**: During execution, counts test cases and POCs every 10 seconds by scanning `testgen/tests/` and `testgen/poc/` directories
5. **Finalize**: Set final status based on exit code, record `finished_at`
6. On exception: status = failed, error_message = exception string

### Scheduler (`TaskScheduler.run`)

- Runs in a background asyncio task
- Every **5 seconds**: checks running count vs `max_jobs`, starts pending tasks up to available capacity
- Each task runs as an independent `asyncio.create_task`

## 6. Log Files Per Task

| File | Path | Source |
|------|------|--------|
| stdout | `workspace/logs/{name}-{version}-stdout.log` | `cargo rapx` stdout |
| stderr | `workspace/logs/{name}-{version}-stderr.log` | `cargo rapx` stderr |
| miri_report | `workspace/repos/{name}-{version}/testgen/miri_report.txt` | Generated by cargo-rapx (optional) |

**Important gap**: There is NO "runner log" — the task executor itself (`TaskExecutor`) does not log its own operations (download progress, extraction steps, errors during preparation, Docker image pulls, etc.). All logging is implicit through Python's `logging` module to the server console/file, but not captured per-task.

## 7. REST API Endpoints

All endpoints defined in `backend/app/main.py`:

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tasks` | Create task (crate_name, optional version) |
| GET | `/api/tasks` | List all tasks |
| GET | `/api/tasks/{id}` | Get task details |
| POST | `/api/tasks/{id}/cancel` | Cancel running task |
| POST | `/api/tasks/{id}/retry` | Reset task to pending |
| DELETE | `/api/tasks/{id}` | Delete task (not running) |
| POST | `/api/tasks/batch-retry` | Batch retry tasks |
| POST | `/api/tasks/batch-delete` | Batch delete tasks |
| GET | `/api/tasks/{id}/stats` | Real-time test case/POC counts from filesystem |
| GET | `/api/tasks/{id}/logs/stdout` | Last N lines of stdout (default 1000) |
| GET | `/api/tasks/{id}/logs/stderr` | Last N lines of stderr |
| GET | `/api/tasks/{id}/logs/miri_report` | Last N lines of miri report |
| GET | `/api/tasks/{id}/logs/stdout/raw` | Full stdout download |
| GET | `/api/tasks/{id}/logs/stderr/raw` | Full stderr download |
| GET | `/api/tasks/{id}/logs/miri_report/raw` | Full miri report download |
| GET | `/api/tasks/{id}/logs/runner` | Last N lines of runner log |
| GET | `/api/tasks/{id}/logs/runner/raw` | Full runner log download |
| GET | `/api/dashboard/stats` | Task count breakdown by status |
| GET | `/api/dashboard/system` | CPU/memory/disk usage |

### Distributed runner control-plane APIs

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/admin/runners` | `X-Admin-Token` | Create runner and return one-time plaintext token |
| GET | `/api/admin/runners` | `X-Admin-Token` | List runners |
| DELETE | `/api/admin/runners/{runner_id}` | `X-Admin-Token` | Soft-disable runner (`enabled=false`) |
| POST | `/api/runners/{runner_id}/heartbeat` | `Authorization: Bearer <runner_token>` | Runner heartbeat |
| POST | `/api/runners/{runner_id}/claim` | `Authorization: Bearer <runner_token>` | Claim one pending task (204 if none) |
| POST | `/api/runners/{runner_id}/tasks/{task_id}/events` | Bearer + lease token | Ingest task lifecycle events |
| POST | `/api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks` | Bearer + lease token | Ingest log chunks (`stdout`/`stderr`/`runner`) |

### Distributed runner provisioning flow

1. Set `[security].admin_token` in `config.toml` (required for runner admin APIs).
2. Create runner via `POST /api/admin/runners` with `X-Admin-Token`; save returned plaintext `token` (shown once).
3. On runner machine set `RUNNER_SERVER_URL`, `RUNNER_ID`, and `RUNNER_TOKEN`.
4. Start runner process with `uv run python -m app.runner` from `backend/`.

### Runner deletion and lease-recovery semantics

- `DELETE /api/admin/runners/{runner_id}` immediately disables auth for that runner; subsequent heartbeat/claim/event/log requests are rejected.
- Tasks already claimed by that runner remain `running` until lease expiry.
- Scheduler periodically requeues expired leased tasks back to `pending` and clears runner lease fields.

## 8. WebSocket Endpoints

| Path | Purpose |
|------|---------|
| `/ws/tasks/{task_id}` | Sends initial task state on connect; broadcasts task updates |
| `/ws/dashboard` | Sends initial dashboard stats on connect |

WebSocket broadcasts task **metadata** only (status, counts, timestamps), NOT log content. Log content is fetched independently via HTTP polling.

### ConnectionManager (`backend/app/api/websocket.py`)

- Global singleton instance
- `task_connections`: Dict[task_id, Set[WebSocket]] — per-task subscriber tracking
- `dashboard_connections`: Set[WebSocket] — dashboard subscriber tracking
- `broadcast_task_update(task_id, data)` / `broadcast_dashboard_update(data)` — fan out to all subscribers, automatically clean up disconnected clients

## 9. Frontend Architecture

### Routes (`frontend/src/router/index.js`)

| Path | Component | Description |
|------|-----------|-------------|
| `/` | redirect | Redirects to `/dashboard` |
| `/dashboard` | Dashboard.vue | Overview stats + system monitor + recent tasks |
| `/tasks` | TaskList.vue | Full task table with filter/sort/batch ops |
| `/tasks/new` | TaskNew.vue | Create single task form |
| `/tasks/batch` | TaskBatch.vue | Batch task creation |
| `/tasks/:id` | TaskDetail.vue | Single task details + log viewer |

### TaskDetail.vue — Layout

1. **Header**: crate name + version, status badge, task ID, action buttons (Back, Cancel/Retry)
2. **Stats Grid**: 5-column bento grid — Status, Test Cases, POCs, Runtime, Exit Code
3. **Details Card**: Created/Started/Finished timestamps, Error message
4. **LogViewer component**: Tabbed log display

### LogViewer.vue — Current Behavior

- **3 tabs**: stdout, stderr, miri_report
- **Auto-refresh**: Polls active tab every **5 seconds** via `api.getLog(taskId, logType, 1000)`
- **Auto-scroll**: Scrolls to bottom on initial load (if `autoScroll` prop is true) and on refresh if user was already at bottom
- **Download**: Button to download full log as file via `/logs/{type}/raw`
- Log content is displayed in a `<pre>` tag inside a dark terminal-style container (`.log-viewer`)
- Each tab loads independently; switching tabs triggers a load if content hasn't been fetched yet

### Data Refresh Patterns

| Component | Method | Interval | What |
|-----------|--------|----------|------|
| Dashboard.vue | HTTP polling | 5s | Stats + full task list |
| TaskDetail.vue | HTTP polling | 3s | Real-time stats (case_count, poc_count) |
| TaskDetail.vue | WebSocket | event-driven | Task status/metadata updates |
| LogViewer.vue | HTTP polling | 5s | Active tab log content (last 1000 lines) |
| SystemMonitor.vue | HTTP polling | 5s | CPU/memory/disk |

### CSS Design System (`frontend/src/style.css`)

- **Tailwind CSS v4** (imported via `@import "tailwindcss"`)
- **Bento grid**: `.bento-grid` (CSS grid) + `.bento-card` (white card with shadow/border/rounded corners)
- **Status badges**: `.status-badge` + `.status-{pending|running|completed|failed|cancelled|timeout|oom}`
- **Log viewer**: `.log-viewer` — dark background (#111827), monospace font, 0.875rem
- **Spinner**: `.spinner` — CSS border animation

### API Client (`frontend/src/services/api.js`)

Axios instance with `/api` baseURL and 30s timeout. Methods:
- `createTask(crate_name, version)`, `getAllTasks()`, `getTask(taskId)`
- `cancelTask(taskId)`, `deleteTask(taskId)`, `retryTask(taskId)`
- `batchRetry(taskIds)`, `batchDelete(taskIds)`
- `getTaskRealtimeStats(taskId)` — test case/POC counts
- `getDashboardStats()`, `getSystemStats()`
- `getLog(taskId, logType, lines)`, `downloadLog(taskId, logType)`

### WebSocket Client (`frontend/src/services/websocket.js`)

Singleton `WebSocketService` class:
- Auto-reconnect on disconnect (5s interval)
- Event-based: `.on(event, callback)` / `.off(event, callback)` / `.emit(event, data)`
- Connects to `ws://host/ws/...` (protocol auto-detected from page URL)
- Events used: `task_update`, `task_created`, `task_completed`, `system_stats`, `connected`, `disconnected`

## 10. Configuration (`config.toml`)

Loaded by `Config.from_file()` on backend, and by Vite at build time for dev proxy.

| Section | Key | Default | Description |
|---------|-----|---------|-------------|
| server | port | 8080 | Backend port |
| server | host | 0.0.0.0 | Bind address |
| workspace | path | ./workspace | Runtime files root |
| execution | max_jobs | 3 | Max concurrent tasks |
| execution | max_memory_gb | 20 | Memory limit per task |
| execution | max_runtime_hours | 24 | Time limit per task |
| execution | execution_mode | systemd | systemd / resource / docker |
| execution | max_cpus | 4 | CPU cores (Docker mode) |
| execution.docker | image | rust-cargo-rapx:latest | Docker image |
| execution.docker | pull_policy | if-not-present | always / if-not-present / never |
| database | path | tasks.db | SQLite path (relative to workspace) |
| logging | level | INFO | Log level |
| logging | console | true | Console output |
| logging | file | true | File output |
| logging | file_path | server.log | Log file path |
| frontend | dev_port | 5173 | Vite dev server port |
| frontend | api_proxy_target | http://localhost:8080 | API proxy |
| frontend | ws_proxy_target | ws://localhost:8080 | WebSocket proxy |
| distributed | enabled | false | Enable distributed runner control plane |
| distributed | lease_ttl_seconds | 30 | Task lease TTL used for claim/recovery |
| distributed | runner_offline_seconds | 30 | Runner offline threshold |
| security | admin_token | "" | Admin token for `/api/admin/runners*` |

## 11. Testing

- Framework: `pytest` with `pytest-asyncio`
- Unit tests: `backend/tests/unit/` — one test file per module
- Integration tests: `backend/tests/integration/` — API endpoints + WebSocket
- Run: `cd backend && uv run pytest` (all) or `uv run pytest tests/unit/ -v`
- Code formatting: `uv run black app/ tests/` (PEP8, required before commit)

## 12. Known Gaps and Limitations

1. **No runner/executor logs**: The `TaskExecutor` does not generate per-task logs of its own operations (download, extract, setup). If executor fails before running `cargo rapx`, only `error_message` in the database captures the issue.

2. **Log polling, not streaming**: Logs are fetched as HTTP snapshots every 5 seconds, not streamed via WebSocket. This introduces up to 5s latency for log updates.

3. **WebSocket not used for logs**: Despite having a WebSocket infrastructure, log content is never sent over WebSocket. Only task metadata (status, counts) is broadcast.

4. **Docker logs are post-hoc**: In Docker mode, stdout/stderr are extracted from the container only after it finishes (not streamed during execution). This means no real-time log updates during Docker execution.

5. **No pagination**: Task list loads all tasks at once. Will need pagination for large deployments.

6. **No end-user auth**: There is runner/admin token auth for control-plane APIs, but no end-user login/session model for UI/task APIs.

7. **Single SQLite connection**: Database uses a single connection with `check_same_thread=False`. Works for low concurrency but may need connection pooling for scale.
