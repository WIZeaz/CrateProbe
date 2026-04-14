# Distributed Runner Service Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split task execution into standalone runner services across multiple LAN machines, add admin-managed runner provisioning (auto-generated token), and keep task/log real-time visibility through the central backend.

**Architecture:** Use a control-plane + pull-runner model. The central FastAPI service owns queue state, leases, auth, and UI APIs; runners authenticate with bearer token, pull work, execute locally, and report heartbeat/events/log chunks. Runner deletion disables auth immediately; running tasks are requeued when lease expires.

**Tech Stack:** FastAPI, SQLite, Python asyncio/httpx, Vue 3, Axios, Tailwind CSS, WebSocket.

---

## Chunk 1: Data Model and Configuration Foundation

### Task 1: Extend database schema for runners and leases

**Files:**
- Modify: `backend/app/database.py`
- Test: `backend/tests/unit/test_database.py`

- [ ] **Step 1: Write failing unit tests for new schema and methods**

```python
def test_init_db_creates_runners_table(db):
    cursor = db.conn.cursor()
    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(runners)")}
    assert {"runner_id", "token_hash", "token_salt", "enabled", "last_seen_at"} <= columns


def test_tasks_table_has_distributed_columns(db):
    cursor = db.conn.cursor()
    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(tasks)")}
    assert {"runner_id", "lease_token", "lease_expires_at", "attempt", "last_event_seq", "cancel_requested"} <= columns


def test_create_and_disable_runner(db):
    db.create_runner("cn-hz-01", "hash", "salt", "rnr_ab", "node", "[]", 2)
    rec = db.get_runner_by_runner_id("cn-hz-01")
    assert rec is not None and rec.enabled is True

    db.disable_runner("cn-hz-01")
    rec = db.get_runner_by_runner_id("cn-hz-01")
    assert rec is not None and rec.enabled is False
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_database.py -k "runners_table or distributed_columns or create_and_disable_runner" -v`

Expected: FAIL with missing table/columns/methods.

- [ ] **Step 3: Implement minimal schema migration and repository methods**

Add in `Database.init_db()`:

```python
cursor.execute("""
    CREATE TABLE IF NOT EXISTS runners (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        runner_id TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL,
        token_salt TEXT NOT NULL,
        token_hint TEXT,
        description TEXT,
        tags_json TEXT NOT NULL DEFAULT '[]',
        capacity_total INTEGER NOT NULL DEFAULT 1,
        enabled INTEGER NOT NULL DEFAULT 1,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        last_seen_at TIMESTAMP
    )
""")
```

Add task-column migrations with `PRAGMA table_info(tasks)` checks and `ALTER TABLE` for:
- `runner_id TEXT`
- `lease_token TEXT`
- `lease_expires_at TIMESTAMP`
- `attempt INTEGER DEFAULT 0`
- `last_event_seq INTEGER DEFAULT 0`
- `cancel_requested INTEGER DEFAULT 0`

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_database.py -k "runners_table or distributed_columns or create_and_disable_runner" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/database.py backend/tests/unit/test_database.py
git commit -m "feat(database): add runners schema and distributed lease fields"
```

### Task 2: Add distributed and admin auth configuration

**Files:**
- Modify: `backend/app/config.py`
- Modify: `config.toml.example`
- Test: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Write failing tests for config keys**

```python
def test_config_loads_distributed_runner_settings(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[distributed]
enabled = true
lease_ttl_seconds = 30
runner_offline_seconds = 30

[security]
admin_token = "admin-secret"
""")
    cfg = Config.from_file(str(config_file))
    assert cfg.distributed_enabled is True
    assert cfg.lease_ttl_seconds == 30
    assert cfg.runner_offline_seconds == 30
    assert cfg.admin_token == "admin-secret"
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_config.py -k "distributed_runner_settings" -v`

Expected: FAIL with missing attributes.

- [ ] **Step 3: Implement config fields and defaults**

Extend `Config` dataclass and parser:
- `distributed_enabled: bool = False`
- `lease_ttl_seconds: int = 30`
- `runner_offline_seconds: int = 30`
- `admin_token: str = ""`

Keep backward compatibility for existing local execution config.

- [ ] **Step 4: Update `config.toml.example` with distributed/security sections**

Add sections:

```toml
[distributed]
enabled = false
lease_ttl_seconds = 30
runner_offline_seconds = 30

[security]
admin_token = "change-me"
```

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_config.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py config.toml.example
git commit -m "feat(config): add distributed mode and admin auth settings"
```

## Chunk 2: Security and Admin Runner Provisioning API

### Task 3: Implement token utilities for runner auth

**Files:**
- Create: `backend/app/security.py`
- Test: `backend/tests/unit/test_security.py`

- [ ] **Step 1: Write failing tests for token generation and verification**

```python
def test_generate_runner_token_prefix_and_length():
    token = generate_runner_token()
    assert token.startswith("rnr_")
    assert len(token) >= 40


def test_hash_verify_roundtrip():
    token = "rnr_test_token"
    salt = generate_salt()
    digest = hash_token(token, salt)
    assert verify_token(token, salt, digest)


def test_hash_verify_rejects_wrong_token():
    salt = generate_salt()
    digest = hash_token("rnr_a", salt)
    assert not verify_token("rnr_b", salt, digest)
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_security.py -v`

Expected: FAIL with module not found.

- [ ] **Step 3: Implement minimal security helpers**

In `backend/app/security.py`, implement:
- `generate_runner_token()` using `secrets.token_urlsafe`
- `generate_salt()` using `secrets.token_bytes`
- `hash_token()` using `hashlib.pbkdf2_hmac`
- `verify_token()` using `hmac.compare_digest`

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_security.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/security.py backend/tests/unit/test_security.py
git commit -m "feat(security): add runner token hashing and verification helpers"
```

### Task 4: Add admin runner create/list/delete APIs with X-Admin-Token

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/integration/test_runner_admin_api.py`

- [ ] **Step 1: Write failing integration tests for admin APIs**

```python
def test_create_runner_returns_one_time_token(client):
    resp = client.post(
        "/api/admin/runners",
        headers={"X-Admin-Token": "admin-secret"},
        json={"runner_id": "cn-hz-01", "capacity_total": 2},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["runner_id"] == "cn-hz-01"
    assert body["token"].startswith("rnr_")


def test_create_runner_rejects_invalid_admin_token(client):
    resp = client.post("/api/admin/runners", headers={"X-Admin-Token": "bad"}, json={"runner_id": "x"})
    assert resp.status_code in (401, 403)


def test_delete_runner_soft_disables(client):
    # create first, then delete
    ...
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_runner_admin_api.py -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement admin auth dependency and routes**

Add routes in `create_app`:
- `POST /api/admin/runners`
- `GET /api/admin/runners`
- `DELETE /api/admin/runners/{runner_id}`

Add dependency:

```python
def require_admin_token(x_admin_token: str = Header(default="")) -> None:
    if not config.admin_token or x_admin_token != config.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")
```

Create response body for create API containing one-time `token` and `token_hint`.

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_runner_admin_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/database.py backend/tests/integration/test_runner_admin_api.py
git commit -m "feat(admin-api): add runner provisioning and deletion endpoints"
```

## Chunk 3: Runner Control-Plane APIs and Lease Recovery

### Task 5: Add authenticated heartbeat and claim endpoints

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/integration/test_runner_control_api.py`

- [ ] **Step 1: Write failing integration tests for runner auth + claim + heartbeat**

```python
def test_heartbeat_rejects_invalid_token(client):
    resp = client.post("/api/runners/cn-hz-01/heartbeat", headers={"Authorization": "Bearer bad"}, json={})
    assert resp.status_code in (401, 403)


def test_claim_returns_204_when_no_task(client, runner_token_headers):
    resp = client.post("/api/runners/cn-hz-01/claim", headers=runner_token_headers, json={"capacity_free": 1})
    assert resp.status_code == 204


def test_claim_assigns_pending_task_and_lease(client, runner_token_headers):
    # create pending task
    ...
    assert resp.status_code == 200
    assert resp.json()["lease_token"]
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "heartbeat or claim" -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement runner auth dependency and endpoints**

Add dependency that validates:
- runner exists and enabled
- bearer token hash matches stored hash

Add routes:
- `POST /api/runners/{runner_id}/heartbeat`
- `POST /api/runners/{runner_id}/claim`

Implement claim atomically (single transaction) to avoid duplicate assignment.

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "heartbeat or claim" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/database.py backend/tests/integration/test_runner_control_api.py
git commit -m "feat(runner-api): add authenticated heartbeat and atomic claim"
```

### Task 6: Add task event and log chunk ingest endpoints

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/app/database.py`
- Test: `backend/tests/integration/test_runner_control_api.py`

- [ ] **Step 1: Write failing tests for events/log chunks idempotency**

```python
def test_event_seq_is_idempotent(client, claimed_task_ctx):
    payload = {"event_seq": 1, "event_type": "started", "lease_token": claimed_task_ctx.lease_token}
    r1 = client.post(claimed_task_ctx.event_url, headers=claimed_task_ctx.headers, json=payload)
    r2 = client.post(claimed_task_ctx.event_url, headers=claimed_task_ctx.headers, json=payload)
    assert r1.status_code == 200 and r2.status_code == 200


def test_log_chunk_written_once_for_same_chunk_seq(client, claimed_task_ctx):
    payload = {"chunk_seq": 1, "content": "line-a\n", "lease_token": claimed_task_ctx.lease_token}
    ...
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "event_seq or chunk_seq" -v`

Expected: FAIL with missing routes.

- [ ] **Step 3: Implement APIs and storage semantics**

Add routes:
- `POST /api/runners/{runner_id}/tasks/{task_id}/events`
- `POST /api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks`

Rules:
- verify lease_token matches active lease
- ignore duplicate seq (return 200 idempotent)
- append log chunk only when `chunk_seq` advances
- broadcast task/dashboard WS updates after event persistence

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "events or logs" -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/main.py backend/app/database.py backend/tests/integration/test_runner_control_api.py
git commit -m "feat(runner-api): ingest task events and log chunks with idempotency"
```

### Task 7: Add lease reconciliation and requeue policy

**Files:**
- Modify: `backend/app/services/scheduler.py`
- Test: `backend/tests/unit/test_scheduler.py`

- [ ] **Step 1: Write failing tests for lease-expiry requeue behavior**

```python
def test_expired_lease_requeues_running_task_in_distributed_mode(scheduler, db):
    # running task with stale lease_expires_at
    ...
    scheduler.reconcile_distributed_leases()
    task = db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.attempt == 1
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_scheduler.py -k "expired_lease_requeues" -v`

Expected: FAIL with missing method.

- [ ] **Step 3: Implement reconciliation path**

Add scheduler method to:
- detect `RUNNING` tasks whose lease expired
- verify runner offline threshold if needed
- reset task to `PENDING`, clear `runner_id/lease_token/lease_expires_at`, increment `attempt`

- [ ] **Step 4: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_scheduler.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/scheduler.py backend/tests/unit/test_scheduler.py
git commit -m "feat(scheduler): requeue expired leased tasks for distributed runners"
```

## Chunk 4: Standalone Runner Program

### Task 8: Implement runner API client

**Files:**
- Create: `backend/app/runner/client.py`
- Test: `backend/tests/unit/test_runner_client.py`

- [ ] **Step 1: Write failing tests for request contracts**

```python
@pytest.mark.asyncio
async def test_claim_handles_204_no_task(client): ...

@pytest.mark.asyncio
async def test_heartbeat_sends_auth_header(client): ...

@pytest.mark.asyncio
async def test_send_event_retries_transient_errors(client): ...
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_runner_client.py -v`

Expected: FAIL with module not found.

- [ ] **Step 3: Implement minimal async client wrapper**

Methods:
- `heartbeat(...)`
- `claim(...)`
- `send_event(...)`
- `send_log_chunk(...)`

Use bearer auth header in one place for consistency.

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_runner_client.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runner/client.py backend/tests/unit/test_runner_client.py
git commit -m "feat(runner): add control-plane client for claim heartbeat and uploads"
```

### Task 9: Implement runner worker loop and execution adapter

**Files:**
- Create: `backend/app/runner/worker.py`
- Create: `backend/app/runner/__main__.py`
- Create: `backend/app/runner/config.py`
- Test: `backend/tests/unit/test_runner_worker.py`

- [ ] **Step 1: Write failing tests for worker control flow**

```python
@pytest.mark.asyncio
async def test_worker_heartbeats_when_idle(...): ...

@pytest.mark.asyncio
async def test_worker_claims_and_reports_started_completed(...): ...

@pytest.mark.asyncio
async def test_worker_reports_failed_on_exception(...): ...
```

- [ ] **Step 2: Run test to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_runner_worker.py -v`

Expected: FAIL with module not found.

- [ ] **Step 3: Implement worker runtime (minimal first)**

Flow:
1. load runner config
2. heartbeat
3. claim
4. if task exists, run command and stream events/log chunks
5. sleep short interval and repeat

Keep local mode intact; this runner process is additive.

- [ ] **Step 4: Run test to verify pass**

Run: `cd backend && uv run pytest tests/unit/test_runner_worker.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/app/runner/config.py backend/app/runner/worker.py backend/app/runner/__main__.py backend/tests/unit/test_runner_worker.py
git commit -m "feat(runner): add standalone worker process for distributed execution"
```

## Chunk 5: Frontend Runner Management and Task Metadata Display

### Task 10: Add frontend API methods and admin token storage

**Files:**
- Modify: `frontend/src/services/api.js`
- Create: `frontend/src/services/adminAuth.js`

- [ ] **Step 1: Add failing expectations in component-level usage (RunnerList to be created)**

Define API surface used later:
- `api.getRunners()`
- `api.createRunner(payload)`
- `api.deleteRunner(runnerId)`

- [ ] **Step 2: Implement `adminAuth` helper**

```js
export function setAdminToken(token) { sessionStorage.setItem('admin_token', token) }
export function getAdminToken() { return sessionStorage.getItem('admin_token') || '' }
export function clearAdminToken() { sessionStorage.removeItem('admin_token') }
```

- [ ] **Step 3: Inject `X-Admin-Token` only for `/api/admin/*` requests**

- [ ] **Step 4: Verify frontend build**

Run: `cd frontend && npm run build`

Expected: BUILD SUCCESS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/services/api.js frontend/src/services/adminAuth.js
git commit -m "feat(frontend-api): add runner admin endpoints and admin token helper"
```

### Task 11: Add Runner Management page and navigation

**Files:**
- Create: `frontend/src/views/RunnerList.vue`
- Modify: `frontend/src/router/index.js`
- Modify: `frontend/src/App.vue`

- [ ] **Step 1: Create `RunnerList.vue` with CRUD UI**

UI requirements:
- list runner rows (`runner_id`, `status`, `last_seen`, `capacity_total`, `tags`)
- create form (`runner_id`, optional desc/tags/capacity)
- one-time token modal after successful creation
- delete action with confirmation dialog

- [ ] **Step 2: Add route and nav link**

Route:

```js
{ path: '/runners', name: 'RunnerList', component: () => import('../views/RunnerList.vue') }
```

- [ ] **Step 3: Verify frontend build**

Run: `cd frontend && npm run build`

Expected: BUILD SUCCESS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/RunnerList.vue frontend/src/router/index.js frontend/src/App.vue
git commit -m "feat(frontend): add runner management page and navigation"
```

### Task 12: Show runner metadata in queue and task detail

**Files:**
- Modify: `frontend/src/views/TaskQueue.vue`
- Modify: `frontend/src/views/TaskDetail.vue`

- [ ] **Step 1: Update queue table to show current `runner_id` for running tasks**
- [ ] **Step 2: Update task detail panel to show `runner_id`, `attempt`, `lease_expires_at`**
- [ ] **Step 3: Keep fallback display for local mode (`-` / `N/A`)**

- [ ] **Step 4: Verify frontend build**

Run: `cd frontend && npm run build`

Expected: BUILD SUCCESS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/TaskQueue.vue frontend/src/views/TaskDetail.vue
git commit -m "feat(frontend): display runner assignment and lease metadata in task views"
```

## Chunk 6: Documentation and End-to-End Verification

### Task 13: Update docs for provisioning and operations

**Files:**
- Modify: `README.md`
- Modify: `Project.md`
- Modify: `config.toml.example`

- [ ] **Step 1: Document admin provisioning flow**

Include:
1. set `security.admin_token`
2. call create-runner API
3. configure runner machine with `runner_id/token/server_url`
4. start runner process

- [ ] **Step 2: Document deletion semantics**

State explicitly:
- deleted runner heartbeat is rejected immediately
- in-flight tasks are requeued when lease expires

- [ ] **Step 3: Commit**

```bash
git add README.md Project.md config.toml.example
git commit -m "docs: add distributed runner provisioning, auth, and lease recovery guide"
```

### Task 14: Full verification and release checklist

**Files:**
- No file changes expected

- [ ] **Step 1: Run full backend tests**

Run: `cd backend && uv run pytest`

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run: `cd frontend && npm run build`

Expected: BUILD SUCCESS.

- [ ] **Step 3: Perform manual smoke test in LAN-like setup**

Checklist:
1. Create runner from admin UI and copy one-time token.
2. Start runner with valid token; verify it appears healthy.
3. Start runner with invalid token; verify heartbeat is rejected.
4. Submit task; verify runner claims task and TaskDetail shows runner_id.
5. Stop runner mid-task; wait lease expiry; verify task returns to pending.
6. Delete runner; verify further heartbeat attempts are rejected.

- [ ] **Step 4: Create final integration commit (if needed)**

```bash
git add -A
git commit -m "feat(distributed-runner): complete control-plane, worker, and UI integration"
```

---

## Notes

- Keep existing local execution mode operational (`distributed.enabled = false`) to avoid breaking current deployments.
- Do not introduce Redis/NATS in this phase; keep architecture simple and aligned with current project constraints.
- Apply TDD discipline strictly per task: fail first, minimal implementation, pass, then commit.
