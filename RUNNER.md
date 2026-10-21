# Runner

Standalone worker that claims jobs from the backend, downloads Rust crates, executes the job's task spec inside Docker, and pushes files and artifacts back to the control plane.

## Quick Start

```bash
cd backend
uv sync
RUNNER_SERVER_URL=http://localhost:8080 \
  RUNNER_ID=local-1 \
  RUNNER_TOKEN=your-runner-token \
  uv run python -m runner
```

## Environment Variables

### Required

| Variable | Description |
|----------|-------------|
| `RUNNER_SERVER_URL` | Base URL of the backend control plane (e.g. `http://localhost:8080`) |
| `RUNNER_ID` | Unique runner identifier reported to the backend |
| `RUNNER_TOKEN` | Authentication token for the runner API |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `RUNNER_POLL_INTERVAL_SECONDS` | `3.0` | Seconds to sleep between job claim attempts when idle |
| `RUNNER_METRICS_INTERVAL_SECONDS` | `10.0` | Minimum seconds between metrics/heartbeat payloads |
| `RUNNER_LOG_FLUSH_INTERVAL_SECONDS` | `3.0` | Seconds between pushing file chunks to the server |
| `RUNNER_LOG_SYNC_INTERVAL_SECONDS` | `2.0` | Seconds between syncing Docker container logs to the host |
| `RUNNER_STATE_SYNC_INTERVAL_SECONDS` | `30.0` | Seconds between sending job state sync payloads to the server |
| `RUNNER_REQUEST_TIMEOUT_SECONDS` | `10.0` | HTTP request timeout when talking to the backend |
| `RUNNER_MAX_JOBS` | `3` | Max concurrent jobs this runner executes in parallel (must be `>= 1`) |
| `RUNNER_DOCKER_PULL_POLICY` | `if-not-present` | Image pull policy: `always`, `if-not-present`, or `never` |
| `RUNNER_DOCKER_MOUNTS` | *(empty)* | Comma-separated extra Docker volume mounts (e.g. `/host:/container:ro`) |
| `RUNNER_WORKSPACE_DIR` | `/workspace` | Host directory where job workspaces, logs, and the task cache are stored |
| `CRATES_IO_USER_AGENT` | `crateprobe-runner` | `User-Agent` header sent to crates.io (should identify your app; see [crates.io Data Access Policy](https://crates.io/data-access)) |
| `CRATES_IO_RATE_LIMIT_RPS` | `1.0` | Max crates.io API metadata requests per second |
| `CRATES_IO_CACHE_TTL_SECONDS` | `300` | How long to cache crate metadata from crates.io API |
| `CRATES_IO_MAX_CONCURRENT_DOWNLOADS` | same as `RUNNER_MAX_JOBS` | Max concurrent `.crate` file downloads from `static.crates.io` |

Per-job resource limits (CPU, memory, timeout) and the Docker image come from the job's `task.toml`, not from runner environment variables.

## Workspace Layout

When `RUNNER_WORKSPACE_DIR` is set to a path (default `/workspace`), the runner creates the following layout:

```
${RUNNER_WORKSPACE_DIR}/
├── logs/
│   └── {job_id}-runner.log         # Runner-side log per job
├── task_cache/                     # Downloaded task zips, extracted and keyed by task id/sha256
└── jobs/
    └── job{job_id}/                # Per-job workspace mounted into the container at the task's workspace path
        ├── <crate source>          # Extracted crate contents with task zip files overlaid
        ├── stdout.log              # Container stdout
        └── stderr.log              # Container stderr
```

## Communication Protocol

The runner communicates with the backend through the following endpoints:

- **`POST /api/runners/{runner_id}/claim`** — Attempt to claim a pending job. The runner reports its current capacity (`jobs` / `max_jobs`) in the payload; the response includes the job's `task_id` and `task_sha256`.
- **`POST /api/runners/{runner_id}/heartbeat`** — Periodic heartbeat that extends active job leases and keeps the runner marked as online.
- **`GET /api/runners/{runner_id}/tasks/{task_id}/zip`** — Download the task definition zip (cached locally under `task_cache/`).
- **`POST /api/runners/{runner_id}/jobs/{job_id}/sync`** — State synchronization for an active job. The payload includes:
  - `sync_seq` — Monotonically increasing sequence number for idempotency.
  - `status` — Job state (`running`, `completed`, `failed`, `timeout`, `oom`, `cancelled`, `runner_failed`).
  - `exit_code`, `message` — Optional terminal-state details.
  The backend ACKs with `last_sync_seq`; the runner retries terminal syncs until ACKed.
- **`POST /api/runners/{runner_id}/jobs/{job_id}/files`** — Unified file push. JSON payload `{lease_token, path, mode, content, chunk_seq?}`; `mode="append"` (logs, ordered by `chunk_seq`) appends, `mode="full"` (watch files) overwrites.
- **`POST /api/runners/{runner_id}/jobs/{job_id}/artifacts`** — Multipart upload of an artifact file after the job finishes.
- **`POST /api/runners/{runner_id}/metrics`** — System metrics payload (`cpu_percent`, `memory_percent`, `disk_percent`, `active_tasks`).
