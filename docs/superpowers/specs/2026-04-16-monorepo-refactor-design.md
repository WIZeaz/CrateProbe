# Monorepo Refactor Design: Independent Frontend, Backend, and Runner

## Overview

Refactor the current Experiment Platform (CrateProbe) into a cleanly separated monorepo where the Python components (Backend, Runner, and shared Core) share a single `pyproject.toml`, while Frontend remains independent. Each component retains its own entry point and configuration:

- `backend/app/` — Pure control plane (FastAPI)
- `backend/runner/` — Standalone task worker (Python)
- `backend/core/` — Shared Python contracts and models
- `frontend/` — Web UI (Vue 3 + Vite)

This reduces dependency-management overhead while keeping runtime boundaries clean.

---

## 1. Target Directory Structure

```
/home/wizeaz/exp-plat/
├── backend/                       # Unified Python project
│   ├── app/                       # Pure control plane (FastAPI)
│   │   ├── main.py                # FastAPI entry point
│   │   ├── config.py              # Backend-only configuration
│   │   ├── database.py            # SQLite operations
│   │   ├── api/                   # REST + WebSocket routes
│   │   ├── services/
│   │   │   ├── scheduler.py       # Queue + lease management only
│   │   │   └── runner_metrics_store.py
│   │   └── security.py            # Runner token generation/verification
│   ├── runner/                    # Standalone worker (same project, independent entry)
│   │   ├── __init__.py
│   │   ├── __main__.py            # Entry point: python -m runner
│   │   ├── config.py              # Environment-variable configuration
│   │   ├── client.py              # RunnerControlClient (httpx)
│   │   ├── worker.py              # Heartbeat + claim + execute loop
│   │   ├── executor.py            # Task execution logic (Docker only)
│   │   ├── docker_runner.py       # Docker execution helper
│   │   └── crates_api.py          # crates.io download client
│   ├── core/                      # Shared models & schemas
│   │   ├── __init__.py
│   │   ├── models.py              # TaskStatus enum
│   │   └── schemas.py             # Pydantic API contracts
│   ├── tests/
│   │   ├── unit/
│   │   │   ├── app/
│   │   │   ├── runner/
│   │   │   └── core/
│   │   └── integration/
│   ├── docker/
│   │   ├── Dockerfile.backend
│   │   ├── Dockerfile.runner
│   │   └── Dockerfile.executor
│   └── pyproject.toml             # Single unified Python manifest
│
├── frontend/                      # Vue 3 + Vite
│   ├── src/
│   ├── .env.development
│   ├── .env.production
│   ├── vite.config.js             # No longer reads config.toml
│   └── package.json
│
├── docker-compose.yml             # Local development orchestration
├── config.toml                    # Backend runtime config only
└── README.md
```

---

## 2. Component Boundaries

### 2.1 Backend (Control Plane Only)

**Responsibilities:**
- Task lifecycle management: create, query, cancel tasks; maintain `TaskStatus`.
- Runner management: registration, token generation/validation, offline detection.
- Task scheduling: maintain the pending queue and lease state; **never execute tasks locally**.
- REST / WebSocket APIs for Frontend and Runner.
- Log storage: receive log chunks from Runners and persist to `workspace/logs/`.

**Removed responsibilities:**
- Local task execution (`task_executor.py`, `local_runner.py`, `resource_limit.py` deleted).
- Docker orchestration for tasks (`docker_runner.py` moved to Runner).
- Crate downloads (`crates_api.py` moved to Runner).

### 2.2 Runner (Standalone Worker)

**Responsibilities:**
- Registration and heartbeat: maintain online state with Backend.
- Task claiming: poll `claim` API to pull pending tasks.
- Task execution: **Docker-only** execution of cargo commands.
- Progress reporting: send task events, log chunks, and metrics to Backend.

**Constraints:**
- Runner is configured purely via environment variables.
- Runner depends on Backend only through its public REST API.
- Systemd and resource fallback execution modes are **removed**.

### 2.3 Frontend

**Responsibilities:**
- Display task queues, task details, runner lists, and metrics dashboards.
- Communicate with Backend via REST and WebSocket.

**Changes:**
- Configuration switches from runtime `config.toml` parsing to build-time `.env` injection.
- `vite.config.js` proxy targets are read from `process.env.VITE_API_BASE_URL` and `VITE_WS_BASE_URL`.

### 2.4 Core (`backend.core`)

**Responsibilities:**
- Provide shared data models and API schemas to both Backend and Runner.
- Remain framework-agnostic except for `pydantic` (used for schema definitions).

**Contents:**
- `backend/core/models.py` — `TaskStatus` enum.
- `backend/core/schemas.py` — Pydantic `BaseModel` definitions for Runner API request/response payloads (e.g., `RunnerHeartbeatPayload`, `TaskClaimResponse`).

**Non-goals:**
- No FastAPI, httpx, Vue, or database dependencies.
- No business logic (token generation remains in Backend `security.py`).

---

## 3. Configuration Strategy

### 3.1 Backend (`config.toml`)

Backend reads a TOML file at runtime. The `execution` block and `distributed.enabled` flag are removed.

```toml
[server]
port = 8080
host = "0.0.0.0"

[workspace]
path = "./workspace"

[database]
path = "tasks.db"

[logging]
level = "INFO"
console = true
file = true
file_path = "server.log"

[distributed]
lease_ttl_seconds = 30
runner_offline_seconds = 30

[security]
admin_token = ""
```

### 3.2 Runner (Environment Variables)

Runner is configured entirely through environment variables. All execution-related settings moved from Backend `config.toml` to Runner ENV.

```bash
RUNNER_SERVER_URL=http://backend:8080
RUNNER_ID=worker-01
RUNNER_TOKEN=...
RUNNER_POLL_INTERVAL_SECONDS=3
RUNNER_METRICS_INTERVAL_SECONDS=10
RUNNER_REQUEST_TIMEOUT_SECONDS=10

RUNNER_MAX_JOBS=3
RUNNER_MAX_MEMORY_GB=20
RUNNER_MAX_RUNTIME_SECONDS=86400
RUNNER_MAX_CPUS=4
RUNNER_DOCKER_IMAGE=rust-cargo-rapx:latest
RUNNER_DOCKER_PULL_POLICY=if-not-present
RUNNER_DOCKER_MOUNTS=/data/cache:/workspace/cache:ro
```

### 3.3 Frontend (`.env` Files)

Frontend uses Vite-native `.env` files.

**`.env.development`:**
```bash
VITE_API_BASE_URL=http://localhost:8080
VITE_WS_BASE_URL=ws://localhost:8080
```

**`.env.production`:**
```bash
VITE_API_BASE_URL=https://api.crateprobe.example.com
VITE_WS_BASE_URL=wss://api.crateprobe.example.com
```

`vite.config.js` reads these values to configure the dev server proxy and can use them in the application via `import.meta.env.VITE_*`.

---

## 4. Docker Compose (Local Development)

A root-level `docker-compose.yml` orchestrates all three services for local development.

```yaml
services:
  backend:
    build:
      context: ./backend
      dockerfile: docker/Dockerfile.backend
    ports:
      - "8080:8080"
    volumes:
      - ./workspace:/app/workspace
      - ./config.toml:/app/config.toml:ro
    environment:
      - CONFIG_PATH=/app/config.toml
    command: ["python", "-m", "app.main"]

  frontend:
    build:
      context: ./frontend
      target: dev
    ports:
      - "5173:5173"
    environment:
      - VITE_API_BASE_URL=http://localhost:8080
      - VITE_WS_BASE_URL=ws://localhost:8080

  runner:
    build:
      context: ./backend
      dockerfile: docker/Dockerfile.runner
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./workspace:/workspace
    environment:
      - RUNNER_SERVER_URL=http://backend:8080
      - RUNNER_ID=local-runner
      - RUNNER_TOKEN=${RUNNER_TOKEN}
      - RUNNER_MAX_JOBS=3
      - RUNNER_MAX_MEMORY_GB=20
      - RUNNER_MAX_RUNTIME_SECONDS=86400
      - RUNNER_DOCKER_IMAGE=rust-cargo-rapx:latest
    depends_on:
      - backend
```

---

## 5. API Contracts

### 5.1 Runner ↔ Backend APIs (Unchanged)

The following endpoints remain unchanged; Runner relocation does not affect their behavior:

- `POST /api/runners/{runner_id}/heartbeat`
- `POST /api/runners/{runner_id}/claim`
- `POST /api/runners/{runner_id}/tasks/{task_id}/events`
- `POST /api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks`
- `POST /api/runners/{runner_id}/metrics`

### 5.2 Shared Pydantic Schemas in `backend.core`

To keep Backend and Runner type-consistent, the following schemas live in `backend.core.schemas`:

```python
from pydantic import BaseModel
from typing import Optional

class RunnerHeartbeatPayload(BaseModel):
    cpu_percent: float
    memory_percent: float
    disk_usage_percent: float
    active_tasks: int

class TaskClaimResponse(BaseModel):
    task_id: int
    lease_token: str
    crate_name: str
    crate_version: str
    command: str
```

Backend uses these for request validation; Runner uses them for request construction.

---

## 6. Code Migration Map

| Source (Current) | Target (After Refactor) | Action |
|------------------|-------------------------|--------|
| `backend/app/models.py` | `backend/core/models.py` | Move `TaskStatus` enum |
| `backend/app/runner/__main__.py` | `backend/runner/__main__.py` | Relocate entry point |
| `backend/app/runner/config.py` | `backend/runner/config.py` | Relocate; add Docker ENV |
| `backend/app/runner/client.py` | `backend/runner/client.py` | Relocate |
| `backend/app/runner/worker.py` | `backend/runner/worker.py` | Relocate |
| `backend/app/services/task_executor.py` | `backend/runner/executor.py` | Migrate execution logic |
| `backend/app/utils/docker_runner.py` | `backend/runner/docker_runner.py` | Migrate Docker helper |
| `backend/app/services/crates_api.py` | `backend/runner/crates_api.py` | Migrate crate download |
| `backend/app/utils/local_runner.py` | — | **Delete** |
| `backend/app/utils/resource_limit.py` | — | **Delete** |
| `backend/app/config.py` (execution/docker blocks) | — | **Delete** from Backend config |
| `frontend/vite.config.js` (toml loader) | `frontend/vite.config.js` (env-based) | Rewrite proxy config |
| `config.toml` (execution block) | Runner ENV / removed | Migrate settings |

---

## 7. Testing Strategy

### 7.1 Unit Tests
All tests live under the unified `backend/tests/` tree:
- `backend/tests/unit/app/` — Control plane logic (API routes, database, scheduler lease expiry).
- `backend/tests/unit/runner/` — ENV parsing, Docker command building, payload serialization.
- `backend/tests/unit/core/` — Pydantic schema validation.

### 7.2 Integration Tests
- `backend/tests/integration/` — Spin up a test Backend and verify end-to-end task flow with a mock Runner client.
- Runner integration tests mock Docker to avoid requiring a Docker daemon in all CI environments.

### 7.3 Compose Smoke Test
- A `docker-compose.test.yml` (or CI step using the main `docker-compose.yml`) verifies that all three services start and a task can be created and claimed.

---

## 8. Known Breaking Changes

1. **Backend no longer executes tasks locally.** Starting Backend alone is insufficient; a Runner must also be running.
2. **`distributed.enabled` is removed.** The system always operates in distributed mode.
3. **Systemd and resource execution modes are removed.** Only Docker mode remains.
4. **Frontend dev server no longer reads `config.toml`.** Developers must use `.env.development` for proxy settings.
5. **Runner moved out of `app/` namespace.** It now lives in `backend/runner/` with its own `__main__.py` entry point, but shares the same `pyproject.toml`.

---

## 9. Implementation Order

1. Create `backend/core/` package and migrate `TaskStatus` + Pydantic schemas.
2. Update Backend (`app/`) to import from `core/` and delete local execution code.
3. Update Backend to remove `distributed.enabled` and `execution.*` configuration.
4. Migrate Runner code from `backend/app/runner/` to `backend/runner/` and add Docker ENV support.
5. Ensure `backend/pyproject.toml` covers all dependencies for `app`, `runner`, and `core`.
6. Add `docker/Dockerfile.backend` and `docker/Dockerfile.runner`.
7. Update Frontend `vite.config.js` to use `.env` injection.
8. Add root-level `docker-compose.yml`.
9. Update and relocate tests under `backend/tests/unit/{app,runner,core}/`.
10. Update documentation (`README.md`, `CLAUDE.md`, `Project.md`).
