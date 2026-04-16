# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is an **Experiment Platform** - an automated Rust crate testing platform that downloads Rust crates, runs experiments using Cargo RAPX, and provides a real-time Web UI to monitor progress and results.

## Architecture

- `backend/app/` — FastAPI control plane
- `backend/runner/` — Standalone worker (entry: `python -m runner`)
- `backend/core/` — Shared models/schemas
- `frontend/` — Vue 3 + Vite

**Backend (Python/FastAPI)**
- `backend/app/main.py` - FastAPI application with REST API and WebSocket endpoints
- `backend/app/services/scheduler.py` - Task scheduler managing concurrent execution
- `backend/app/services/task_executor.py` - Downloads crates and executes cargo commands
- `backend/app/database.py` - SQLite database for task persistence
- `backend/app/models.py` - TaskStatus enum (pending, running, completed, failed, cancelled, timeout, oom)
- `backend/app/config.py` - Configuration loaded from `config.toml`

**Frontend (Vue 3 + Vite)**
- `frontend/src/` - Vue 3 Composition API components
- `frontend/src/views/` - Page components (Dashboard, TaskList, TaskNew, TaskDetail)
- `frontend/src/services/api.js` - Axios REST client
- `frontend/src/services/websocket.js` - WebSocket connection manager
- `frontend/vite.config.js` - Vite dev server with proxy to backend

**Configuration**
- `config.toml` - Backend configuration (see `config.toml.example`)

## Common Commands

**Backend (requires `uv`)**
```bash
cd backend

# Install dependencies
uv sync

# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/test_config.py

# Run single test
uv run pytest tests/unit/test_config.py::test_config_loads_from_file -v

# Start backend server
uv run python -m app.main
```

**Runner**
```bash
cd backend
RUNNER_SERVER_URL=http://localhost:8080 RUNNER_ID=local RUNNER_TOKEN=... uv run python -m runner
```

**Frontend**
```bash
cd frontend

# Install dependencies
npm install

# Start development server
npm run dev

# Build for production
npm run build
```

**Setup from scratch**
```bash
# 1. Create config file
cp config.toml.example config.toml

# 2. Start backend (terminal 1)
cd backend && uv sync && uv run python -m app.main

# 3. Start runner (terminal 2)
cd backend && RUNNER_SERVER_URL=http://localhost:8080 RUNNER_ID=local RUNNER_TOKEN=... uv run python -m runner

# 4. Start frontend (terminal 3)
cd frontend && npm install && npm run dev

# 5. Open http://localhost:5173
```

## Key Design Patterns

**Task Lifecycle**
Tasks flow through states: `pending` -> `running` -> (`completed` | `failed` | `cancelled` | `timeout` | `oom`)

**Resource Limits**
Tasks execute with resource constraints via `systemd-run` (preferred) or `resource` module fallback. Limits configured in `config.toml`: `max_memory_gb`, `max_runtime_hours`, `max_jobs` (concurrency).

**WebSocket Updates**
- `/ws/dashboard` - Real-time dashboard stats
- `/ws/tasks/{task_id}` - Individual task status updates

**Workspace Structure**
```
workspace/
├── repos/          # Downloaded crate source code
├── logs/           # stdout/stderr logs per task
└── tasks.db        # SQLite database
```

**Testing**
- Uses pytest with asyncio support (`pytest.ini` config)
- Unit tests in `tests/unit/`
- Integration tests in `tests/integration/`
- TDD approach preferred for new features
