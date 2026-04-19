# Runner Delete/Disable/Enable Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Separate runner admin semantics so `DELETE` truly deletes, and add explicit `disable`/`enable` APIs with matching UI actions.

**Architecture:** Keep the existing runner auth and lease-recovery flow unchanged, and only split admin control actions at the API and UI layers. Implement behavior behind clear database methods (`disable`, `enable`, `delete`) and make handlers idempotent for `disable`/`enable` by resolving existence before update and always returning current runner state.

**Tech Stack:** FastAPI, SQLite (raw SQL), pytest, Vue 3, Axios

---

## File Map

- Modify: `backend/app/database.py` (runner persistence methods)
- Modify: `backend/app/main.py` (runner admin routes and semantics)
- Modify: `backend/tests/unit/test_database.py` (runner DB behavior tests)
- Modify: `backend/tests/integration/test_runner_admin_api.py` (admin API contract tests)
- Modify: `backend/tests/integration/test_runner_control_api.py` (disable/delete lease/auth regressions)
- Modify: `frontend/src/services/api.js` (admin runner API client methods)
- Modify: `frontend/src/views/RunnerList.vue` (Delete/Disable/Enable UI and messaging)
- Modify: `Project.md` (document new runner API contract)

## Chunk 1: Backend API + Database + Tests

### Task 1: Add runner DB methods for enable/delete and keep disable idempotent-friendly

**Files:**
- Modify: `backend/app/database.py`
- Test: `backend/tests/unit/test_database.py`

- [ ] **Step 1: Write failing unit tests for enable and delete_runner**

Add tests in `backend/tests/unit/test_database.py`:

```python
def test_enable_runner_sets_enabled_true(db):
    db.create_runner("runner-enable", "hash", "salt")
    db.disable_runner("runner-enable")

    enabled = db.enable_runner("runner-enable")
    assert enabled is True

    runner = db.get_runner_by_runner_id("runner-enable")
    assert runner is not None
    assert runner.enabled is True


def test_delete_runner_removes_record(db):
    db.create_runner("runner-delete", "hash", "salt")

    deleted = db.delete_runner("runner-delete")
    assert deleted is True
    assert db.get_runner_by_runner_id("runner-delete") is None


def test_disable_enable_runner_are_idempotent_for_existing_runner(db):
    db.create_runner("runner-idempotent", "hash", "salt")

    assert db.disable_runner("runner-idempotent") is True
    assert db.disable_runner("runner-idempotent") is True

    assert db.enable_runner("runner-idempotent") is True
    assert db.enable_runner("runner-idempotent") is True


def test_disable_enable_delete_runner_return_false_for_missing_runner(db):
    assert db.disable_runner("missing-runner") is False
    assert db.enable_runner("missing-runner") is False
    assert db.delete_runner("missing-runner") is False
```

- [ ] **Step 2: Run targeted unit tests to verify failure**

Run: `cd backend && uv run pytest tests/unit/test_database.py -k "enable_runner or delete_runner or idempotent or missing_runner" -v`

Expected: FAIL because `enable_runner` / `delete_runner` are not implemented.

- [ ] **Step 3: Implement minimal database methods**

In `backend/app/database.py` (runner existence semantics for idempotent calls):

```python
def disable_runner(self, runner_id: str) -> bool:
    cursor = self.conn.cursor()
    row = cursor.execute(
        "SELECT 1 FROM runners WHERE runner_id = ?",
        (runner_id,),
    ).fetchone()
    if row is None:
        return False
    cursor.execute("UPDATE runners SET enabled = 0 WHERE runner_id = ?", (runner_id,))
    self.conn.commit()
    return True


def enable_runner(self, runner_id: str) -> bool:
    cursor = self.conn.cursor()
    row = cursor.execute(
        "SELECT 1 FROM runners WHERE runner_id = ?",
        (runner_id,),
    ).fetchone()
    if row is None:
        return False
    cursor.execute("UPDATE runners SET enabled = 1 WHERE runner_id = ?", (runner_id,))
    self.conn.commit()
    return True


def delete_runner(self, runner_id: str) -> bool:
    cursor = self.conn.cursor()
    cursor.execute("DELETE FROM runners WHERE runner_id = ?", (runner_id,))
    self.conn.commit()
    return cursor.rowcount > 0
```

- [ ] **Step 4: Re-run targeted unit tests**

Run: `cd backend && uv run pytest tests/unit/test_database.py -k "enable_runner or delete_runner or idempotent or missing_runner" -v`

Expected: PASS.

- [ ] **Step 5: Commit backend DB method changes**

```bash
git add backend/app/database.py backend/tests/unit/test_database.py
git commit -m "feat(backend): add runner enable and delete database operations"
```

### Task 2: Split admin runner routes into delete/disable/enable semantics

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_runner_admin_api.py`

- [ ] **Step 1: Write failing integration tests for new admin runner contract**

Update/add tests in `backend/tests/integration/test_runner_admin_api.py`:

```python
def test_delete_runner_truly_deletes(client):
    client.post("/api/admin/runners", headers=_admin_headers(), json={"runner_id": "runner-delete"})

    delete_resp = client.delete("/api/admin/runners/runner-delete", headers=_admin_headers())
    assert delete_resp.status_code == 200

    list_resp = client.get("/api/admin/runners", headers=_admin_headers())
    ids = {item["runner_id"] for item in list_resp.json()}
    assert "runner-delete" not in ids


def test_disable_runner_sets_enabled_false(client):
    client.post("/api/admin/runners", headers=_admin_headers(), json={"runner_id": "runner-disable"})
    resp = client.post("/api/admin/runners/runner-disable/disable", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False


def test_enable_runner_sets_enabled_true(client):
    client.post("/api/admin/runners", headers=_admin_headers(), json={"runner_id": "runner-enable"})
    client.post("/api/admin/runners/runner-enable/disable", headers=_admin_headers())
    resp = client.post("/api/admin/runners/runner-enable/enable", headers=_admin_headers())
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_runner_admin_api.py -v`

Expected: FAIL because `DELETE` still disables and `/disable` `/enable` routes are missing.

- [ ] **Step 3: Implement route changes in FastAPI app**

In `backend/app/main.py`:

- Keep dependency-based admin auth.
- Change `DELETE /api/admin/runners/{runner_id}` handler to:
  1) lookup runner and 404 if missing,
  2) keep pre-delete snapshot,
  3) call `db.delete_runner(runner_id)`,
  4) return snapshot as `RunnerResponse`.
- Add `POST /api/admin/runners/{runner_id}/disable`:
  1) lookup runner and 404 if missing,
  2) call `db.disable_runner(runner_id)`,
  3) fetch and return updated runner.
- Add `POST /api/admin/runners/{runner_id}/enable` with symmetric logic.
- Expose scheduler for integration tests:

```python
app.state.scheduler = scheduler
```

- [ ] **Step 4: Re-run admin integration tests**

Run: `cd backend && uv run pytest tests/integration/test_runner_admin_api.py -v`

Expected: PASS.

- [ ] **Step 5: Commit admin route split**

```bash
git add backend/app/main.py backend/tests/integration/test_runner_admin_api.py
git commit -m "feat(api): split runner delete disable and enable actions"
```

### Task 3: Update control-plane regressions for disable and lease behavior

**Files:**
- Modify: `backend/tests/integration/test_runner_control_api.py`

- [ ] **Step 1: Write failing regression tests for disable/enable auth and lease recovery**

Add/update tests in `backend/tests/integration/test_runner_control_api.py`:

```python
def test_disabled_runner_rejected_by_runner_endpoints(client):
    token = _create_runner(client, "runner-disabled-auth")
    client.post("/api/admin/runners/runner-disabled-auth/disable", headers=_admin_headers())

    heartbeat = client.post(
        "/api/runners/runner-disabled-auth/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat.status_code == 403


def test_deleted_runner_rejected_by_runner_endpoints(client):
    token = _create_runner(client, "runner-deleted-auth")
    client.delete("/api/admin/runners/runner-deleted-auth", headers=_admin_headers())

    heartbeat = client.post(
        "/api/runners/runner-deleted-auth/heartbeat",
        headers=_runner_headers(token),
    )
    claim = client.post(
        "/api/runners/runner-deleted-auth/claim",
        headers=_runner_headers(token),
    )
    assert heartbeat.status_code == 403
    assert claim.status_code == 403


def test_enable_restores_runner_auth(client):
    token = _create_runner(client, "runner-enable-auth")
    client.post("/api/admin/runners/runner-enable-auth/disable", headers=_admin_headers())
    client.post("/api/admin/runners/runner-enable-auth/enable", headers=_admin_headers())

    heartbeat = client.post(
        "/api/runners/runner-enable-auth/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat.status_code == 200
```

Add lease-regression tests (disable/delete after claim):

```python
def test_claimed_task_stays_running_until_lease_expiry_after_disable(client, app):
    token = _create_runner(client, "runner-lease-disable")
    create_task = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_task.json()["task_id"]

    claim = client.post(
        "/api/runners/runner-lease-disable/claim",
        headers=_runner_headers(token),
    )
    assert claim.status_code == 200

    disable = client.post(
        "/api/admin/runners/runner-lease-disable/disable",
        headers=_admin_headers(),
    )
    assert disable.status_code == 200

    pre = client.get(f"/api/tasks/{task_id}")
    assert pre.status_code == 200
    assert pre.json()["status"] == "running"

    time.sleep(1.2)
    app.state.scheduler.reconcile_expired_leases()

    post = client.get(f"/api/tasks/{task_id}")
    assert post.status_code == 200
    assert post.json()["status"] == "pending"


def test_claimed_task_stays_running_until_lease_expiry_after_delete(client, app):
    token = _create_runner(client, "runner-lease-delete")
    create_task = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_task.json()["task_id"]

    claim = client.post(
        "/api/runners/runner-lease-delete/claim",
        headers=_runner_headers(token),
    )
    assert claim.status_code == 200

    delete = client.delete(
        "/api/admin/runners/runner-lease-delete",
        headers=_admin_headers(),
    )
    assert delete.status_code == 200

    pre = client.get(f"/api/tasks/{task_id}")
    assert pre.status_code == 200
    assert pre.json()["status"] == "running"

    time.sleep(1.2)
    app.state.scheduler.reconcile_expired_leases()

    post = client.get(f"/api/tasks/{task_id}")
    assert post.status_code == 200
    assert post.json()["status"] == "pending"
```

Implementation notes for tests:

- Keep shared fixture `lease_ttl_seconds=60` to avoid side effects on existing non-lease tests.
- For lease-expiry regression tests only, use dedicated app/client fixture (or per-test config override) with `distributed_enabled=True` and `lease_ttl_seconds=1`.
- Add `import time` in test module for lease-expiry wait.
- Add lease field assertions after recovery (`runner_id is None`, `lease_token is None`, `lease_expires_at is None`).

- [ ] **Step 2: Run targeted control-plane tests to verify failure**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "disabled_runner or deleted_runner or enable_restores or lease_expiry" -v`

Expected: FAIL before all test updates/route rewiring are complete.

- [ ] **Step 3: Update existing tests that relied on DELETE-as-disable**

Replace any `client.delete("/api/admin/runners/{id}")` used for disabling with `client.post("/api/admin/runners/{id}/disable")` where the semantic intent is disable, not deletion.

- [ ] **Step 4: Run full backend regression scope for runner APIs**

Run:

`cd backend && uv run pytest tests/integration/test_runner_admin_api.py tests/integration/test_runner_control_api.py tests/unit/test_database.py -v`

Expected: PASS.

- [ ] **Step 5: Commit control-plane test regressions**

```bash
git add backend/tests/integration/test_runner_control_api.py
git commit -m "test(backend): cover runner disable enable and lease recovery semantics"
```

## Chunk 2: Frontend + Docs + Final Verification

### Task 4: Split frontend admin runner API client methods

**Files:**
- Modify: `frontend/src/services/api.js`

- [ ] **Step 1: Add failing frontend usage expectation in RunnerList workflow (manual trigger)**

Prepare to call three explicit methods from `RunnerList.vue` (`deleteRunner`, `disableRunner`, `enableRunner`) and verify runtime error appears before adding methods.

- [ ] **Step 2: Add API methods**

In `frontend/src/services/api.js` add:

```javascript
async disableRunner(runnerId) {
  const response = await api.post(`/admin/runners/${runnerId}/disable`)
  return response.data
},

async enableRunner(runnerId) {
  const response = await api.post(`/admin/runners/${runnerId}/enable`)
  return response.data
},
```

Keep existing `deleteRunner` method as-is path-wise.

- [ ] **Step 3: Commit API client split**

```bash
git add frontend/src/services/api.js
git commit -m "feat(frontend): add runner disable and enable API clients"
```

### Task 5: Update RunnerList actions and messaging

**Files:**
- Modify: `frontend/src/views/RunnerList.vue`

- [ ] **Step 1: Add action handlers for disable/enable and separate loading state**

Implement:

- `disablingRunnerId` and `enablingRunnerId` refs.
- `disableRunner(runner)` and `enableRunner(runner)` methods calling new API functions.
- Keep `deleteRunner` but update confirmation message to include irreversible deletion.
- Success path contract for all three actions: `await fetchRunners()` after API success.
- Failure path contract for all three actions: follow existing UI error style (`alert` or existing error text), do not silently swallow errors.

- [ ] **Step 2: Update table actions UI**

Render action buttons by state:

- Always show `Delete`.
- Show `Disable` when `runner.enabled` is true.
- Show `Enable` when `runner.enabled` is false.

Ensure each button is disabled while its corresponding action is in progress.

- [ ] **Step 3: Quick manual smoke check in dev mode**

Run:

1) `cd backend && uv run python -m app.main`
2) `cd frontend && npm run dev`

Preconditions:

- `config.toml` has `security.admin_token` set to a non-empty value.
- In UI `Settings` page, admin token is configured and validated.
- At least one runner exists (create from Runner page if empty).

Manual checks:

- disable keeps runner row and flips to disabled.
- enable restores to enabled.
- delete removes row.

- [ ] **Step 4: Build frontend to verify no compile regressions**

Run: `cd frontend && npm run build`

Expected: build success.

- [ ] **Step 5: Commit RunnerList UI split**

```bash
git add frontend/src/views/RunnerList.vue
git commit -m "feat(frontend): separate runner delete disable and enable actions"
```

### Task 6: Update project documentation and run final verification

**Files:**
- Modify: `Project.md`

- [ ] **Step 1: Update runner API table and semantics text**

In `Project.md` update distributed runner control-plane section:

- `DELETE /api/admin/runners/{runner_id}` description to true deletion.
- Add rows for:
  - `POST /api/admin/runners/{runner_id}/disable`
  - `POST /api/admin/runners/{runner_id}/enable`
- Update lease-recovery semantics paragraph accordingly.

- [ ] **Step 1.1: Audit and update other docs that mention runner delete/disable semantics**

Search docs for stale wording and update where needed:

Run: `cd /home/wizeaz/exp-plat && rg "admin/runners|runner.*delete|soft-disable|disable" docs frontend/README.md backend/README.md README.md -n`

Expected:

- If matches include semantic references, update each to reflect delete/disable/enable split.
- If no additional semantic references exist, record "none found" in implementation notes/PR summary.

- [ ] **Step 2: Run focused final verification commands**

Run:

1) `cd backend && uv run pytest tests/integration/test_runner_admin_api.py tests/integration/test_runner_control_api.py tests/unit/test_database.py -v`
2) `cd frontend && npm run build`

Expected: all pass.

- [ ] **Step 3: Commit docs and any final test fixes**

```bash
git add Project.md docs frontend/README.md backend/README.md README.md
git commit -m "docs: clarify runner delete disable and enable API semantics"
```

## Execution Notes

- Prefer small, sequential commits per task group; do not squash during implementation.
- Do not alter unrelated runner metrics/chart code.
- Keep scope strictly to runner admin semantics split and associated regressions.
