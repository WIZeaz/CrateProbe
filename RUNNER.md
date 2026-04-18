# Runner

Standalone worker that claims tasks from the backend, downloads Rust crates, runs experiments inside Docker, and uploads logs back to the control plane.

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
| `RUNNER_POLL_INTERVAL_SECONDS` | `3.0` | Seconds to sleep between task claim attempts when idle |
| `RUNNER_METRICS_INTERVAL_SECONDS` | `10.0` | Minimum seconds between metrics/heartbeat payloads |
| `RUNNER_REQUEST_TIMEOUT_SECONDS` | `10.0` | HTTP request timeout when talking to the backend |
| `RUNNER_MAX_JOBS` | `3` | *(Reserved)* Max concurrent jobs configured locally |
| `RUNNER_MAX_MEMORY_GB` | `20` | Docker container memory limit in GB |
| `RUNNER_MAX_RUNTIME_SECONDS` | `86400` | Docker container max runtime before force stop |
| `RUNNER_MAX_CPUS` | `4` | CPU limit for Docker containers |
| `RUNNER_DOCKER_IMAGE` | `rust-cargo-rapx:latest` | Docker image used to execute tasks |
| `RUNNER_DOCKER_PULL_POLICY` | `if-not-present` | Image pull policy: `always`, `if-not-present`, or `never` |
| `RUNNER_DOCKER_MOUNTS` | *(empty)* | Comma-separated extra Docker volume mounts (e.g. `/host:/container:ro`) |
| `RUNNER_WORKSPACE_DIR` | `/workspace` | Host directory where logs, repos, and task workspaces are stored |

## Workspace Layout

When `RUNNER_WORKSPACE_DIR` is set to a path (default `/workspace`), the runner creates the following layout:

```
${RUNNER_WORKSPACE_DIR}/
├── logs/                           # stdout, stderr, and runner logs per task
│   ├── {task_id}-stdout.log
│   ├── {task_id}-stderr.log
│   └── {task_id}-runner.log
├── repos/                          # Downloaded .crate files and temp extraction
└── {crate_name}-{version}/         # Per-task workspace mounted into Docker
    └── testgen/
        ├── tests/                  # Generated test cases
        ├── poc/                    # Proof-of-concept outputs
        ├── miri_report.txt
        └── stats.yaml
```
