# Runner Self-Reported Capacity Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade runner/backend claim protocol so runner reports `jobs` and `max_jobs`, backend gates claims on those values, and runner heartbeats stay decoupled from task execution.

**Architecture:** Keep claim as single-task pull and enforce capacity with runner-reported values per request. Refactor runner worker into slot-based concurrent execution with process-level heartbeat loop that is independent of task lifecycle. Add backend validation guard (`claim_max_jobs_hard_limit`) to prevent pathological `max_jobs` input.

**Tech Stack:** Python 3, FastAPI, Pydantic, asyncio/threading, httpx, pytest, uv

---

## File Structure and Responsibilities

- Modify: `backend/app/main.py`
  - Add claim request schema (`jobs`, `max_jobs`, optional `runner_id`) and capacity gate logic in claim endpoint.
- Modify: `backend/app/config.py`
  - Add backend guardrail setting `claim_max_jobs_hard_limit` with TOML parsing.
- Modify: `config.toml.example`
  - Expose distributed config key for claim hard limit.
- Modify: `backend/runner/worker.py`
  - Move to inflight-task concurrency model and process-lifetime heartbeat loop.
- Modify: `backend/runner/__main__.py`
  - Wire `max_jobs` from `RunnerConfig` into `RunnerWorker`.
- Modify: `backend/tests/integration/test_runner_control_api.py`
  - Update claim requests to new payload and add contract behavior coverage.
- Modify: `backend/tests/unit/test_main.py`
  - Update helper claim requests for new protocol.
- Modify: `backend/tests/unit/test_config.py`
  - Add tests for `claim_max_jobs_hard_limit` defaults and TOML loading.
- Modify: `backend/tests/unit/runner/test_worker.py`
  - Add worker capacity/concurrency/heartbeat-lifecycle tests.
- Modify: `backend/tests/unit/runner/test_runner_client.py`
  - Assert claim payload shape for updated runner claim contract.

## Chunk 1: Backend Claim Contract and Guardrails

### Task 1: Add claim payload schema and capacity gate (TDD)

**Files:**
- Modify: `backend/tests/integration/test_runner_control_api.py`
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_runner_control_api.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_claim_requires_jobs_and_max_jobs_payload`**

```python
def test_claim_requires_jobs_and_max_jobs_payload(client):
    token = _create_runner(client, "runner-claim-contract")
    response = client.post(
        "/api/runners/runner-claim-contract/claim",
        headers=_runner_headers(token),
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Write failing test `test_claim_returns_204_when_runner_reports_capacity_full`**

```python
def test_claim_returns_204_when_runner_reports_capacity_full(client):
    token = _create_runner(client, "runner-capacity-full")
    create_task = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    assert create_task.status_code == 200

    response = client.post(
        "/api/runners/runner-capacity-full/claim",
        headers=_runner_headers(token),
        json={"jobs": 3, "max_jobs": 3},
    )
    assert response.status_code == 204
```

- [ ] **Step 3: Add regression test `test_claim_ignores_body_runner_id_and_uses_path_runner_id`**

```python
def test_claim_ignores_body_runner_id_and_uses_path_runner_id(client):
    token = _create_runner(client, "runner-path-authority")
    create_task = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    assert create_task.status_code == 200

    response = client.post(
        "/api/runners/runner-path-authority/claim",
        headers=_runner_headers(token),
        json={"runner_id": "other-runner", "jobs": 0, "max_jobs": 1},
    )
    assert response.status_code == 200
    assert response.json()["runner_id"] == "runner-path-authority"
```

- [ ] **Step 4: Run RED for new claim contract tests**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_requires_jobs_and_max_jobs_payload tests/integration/test_runner_control_api.py::test_claim_returns_204_when_runner_reports_capacity_full tests/integration/test_runner_control_api.py::test_claim_ignores_body_runner_id_and_uses_path_runner_id -v`

Expected: FAIL (new claim contract tests fail before implementation)

- [ ] **Step 5: Implement minimal claim request model and endpoint gate**

```python
class ClaimTaskRequest(BaseModel):
    runner_id: Optional[str] = None
    jobs: int
    max_jobs: int


@app.post("/api/runners/{runner_id}/claim", ...)
async def claim_task(
    runner_id: str,
    request: ClaimTaskRequest,
    _auth: None = Depends(require_runner_auth),
):
    if request.jobs >= request.max_jobs:
        return PlainTextResponse(status_code=204, content="")

    task = db.claim_pending_task(runner_id, config.lease_ttl_seconds)
    ...
```

- [ ] **Step 6: Update claim callsites broken by mandatory claim payload**

- Update `_create_and_claim_task` and direct `/claim` calls in `backend/tests/integration/test_runner_control_api.py` to send claim JSON (for example `{"jobs": 0, "max_jobs": 1}`).
- Update `claim_task` helper in `backend/tests/unit/test_main.py` to send claim JSON.

- [ ] **Step 7: Run GREEN for claim contract and compatibility smoke tests**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_requires_jobs_and_max_jobs_payload tests/integration/test_runner_control_api.py::test_claim_returns_204_when_runner_reports_capacity_full tests/integration/test_runner_control_api.py::test_claim_ignores_body_runner_id_and_uses_path_runner_id tests/integration/test_runner_control_api.py::test_claim_assigns_pending_task_and_returns_lease_token tests/unit/test_main.py::test_runner_helpers_can_create_and_claim_task -v`

Expected: PASS

- [ ] **Step 8: Commit task changes**

```bash
git add backend/app/main.py backend/tests/integration/test_runner_control_api.py backend/tests/unit/test_main.py
git commit -m "feat(runner-api): gate claim by runner-reported jobs and max_jobs"
```

### Task 2: Add claim max-jobs hard limit config (TDD)

**Files:**
- Modify: `backend/tests/unit/test_config.py`
- Modify: `backend/app/config.py`
- Modify: `config.toml.example`
- Test: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Write failing test `test_config_defaults_claim_max_jobs_hard_limit_to_256`**

```python
def test_config_defaults_claim_max_jobs_hard_limit_to_256():
    cfg = Config.from_file("nonexistent.toml")
    assert cfg.claim_max_jobs_hard_limit == 256
```

- [ ] **Step 2: Write failing test `test_config_loads_claim_max_jobs_hard_limit_from_file`**

```python
def test_config_loads_claim_max_jobs_hard_limit_from_file(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[distributed]
lease_ttl_seconds = 30
runner_offline_seconds = 30
claim_max_jobs_hard_limit = 128
""")
    cfg = Config.from_file(str(config_file))
    assert cfg.claim_max_jobs_hard_limit == 128
```

- [ ] **Step 3: Run RED for new config tests**

Run: `uv run --directory backend pytest tests/unit/test_config.py::test_config_defaults_claim_max_jobs_hard_limit_to_256 tests/unit/test_config.py::test_config_loads_claim_max_jobs_hard_limit_from_file -v`

Expected: FAIL (missing config field)

- [ ] **Step 4: Implement minimal config field parsing and example config key**

```python
@dataclass
class Config:
    ...
    claim_max_jobs_hard_limit: int = 256

    @classmethod
    def from_file(cls, path: str) -> "Config":
        ...
        distributed = data.get("distributed", {})
        return cls(
            ...,
            claim_max_jobs_hard_limit=distributed.get("claim_max_jobs_hard_limit", 256),
        )
```

Add to `config.toml.example`:

```toml
[distributed]
lease_ttl_seconds = 30
runner_offline_seconds = 30
claim_max_jobs_hard_limit = 256
```

- [ ] **Step 5: Run GREEN for config tests**

Run: `uv run --directory backend pytest tests/unit/test_config.py::test_config_defaults_claim_max_jobs_hard_limit_to_256 tests/unit/test_config.py::test_config_loads_claim_max_jobs_hard_limit_from_file -v`

Expected: PASS

- [ ] **Step 6: Commit task changes**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py config.toml.example
git commit -m "feat(config): add claim max jobs hard limit for runner claims"
```

### Task 3: Update claim callsites and cover invalid payload branches (TDD)

**Files:**
- Modify: `backend/tests/integration/test_runner_control_api.py`
- Modify: `backend/tests/unit/test_main.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_runner_control_api.py`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: Write failing test `test_claim_rejects_jobs_greater_than_max_jobs`**

```python
def test_claim_rejects_jobs_greater_than_max_jobs(client):
    token = _create_runner(client, "runner-invalid-jobs")
    response = client.post(
        "/api/runners/runner-invalid-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": 2, "max_jobs": 1},
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Write failing test `test_claim_invalid_jobs_warning_contains_runner_id`**

```python
def test_claim_invalid_jobs_warning_contains_runner_id(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-invalid-jobs-log")

    response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=auth_headers(token, request_id="req-invalid-jobs"),
        json={"jobs": 2, "max_jobs": 1},
    )
    assert response.status_code == 422

    record = next(r for r in caplog.records if "invalid claim payload" in r.message.lower())
    assert record.runner_id == runner_id
```

- [ ] **Step 3: Run RED for invalid-jobs loop**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_rejects_jobs_greater_than_max_jobs tests/unit/test_main.py::test_claim_invalid_jobs_warning_contains_runner_id -v`

Expected: FAIL

- [ ] **Step 4: Implement `jobs > max_jobs` validation (must execute before `jobs >= max_jobs` 204 gate) and warning log in `claim_task`**

```python
if request.jobs > request.max_jobs:
    logger.warning(
        "invalid claim payload: jobs greater than max_jobs",
        extra={"runner_id": runner_id, "jobs": request.jobs, "max_jobs": request.max_jobs},
    )
    raise HTTPException(status_code=422, detail="jobs must be <= max_jobs")
```

- [ ] **Step 5: Run GREEN for invalid-jobs loop**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_rejects_jobs_greater_than_max_jobs tests/unit/test_main.py::test_claim_invalid_jobs_warning_contains_runner_id -v`

Expected: PASS

- [ ] **Step 6: Write failing test `test_claim_rejects_max_jobs_over_hard_limit`**

```python
def test_claim_rejects_max_jobs_over_hard_limit(client):
    token = _create_runner(client, "runner-over-limit")
    response = client.post(
        "/api/runners/runner-over-limit/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 9999},
    )
    assert response.status_code == 422
```

- [ ] **Step 7: Write failing test `test_claim_max_jobs_overflow_warning_contains_runner_id`**

```python
def test_claim_max_jobs_overflow_warning_contains_runner_id(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-max-overflow-log")

    response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=auth_headers(token, request_id="req-max-overflow"),
        json={"jobs": 0, "max_jobs": 9999},
    )
    assert response.status_code == 422

    record = next(r for r in caplog.records if "invalid claim payload" in r.message.lower())
    assert record.runner_id == runner_id
```

- [ ] **Step 8: Write failing test `test_claim_rejects_negative_jobs`**

```python
def test_claim_rejects_negative_jobs(client):
    token = _create_runner(client, "runner-negative-jobs")
    response = client.post(
        "/api/runners/runner-negative-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": -1, "max_jobs": 1},
    )
    assert response.status_code == 422
```

- [ ] **Step 9: Write failing test `test_claim_rejects_zero_max_jobs`**

```python
def test_claim_rejects_zero_max_jobs(client):
    token = _create_runner(client, "runner-zero-max")
    response = client.post(
        "/api/runners/runner-zero-max/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 0},
    )
    assert response.status_code == 422
```

- [ ] **Step 10: Run RED for max-jobs-overflow loop and schema-bound tests**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_rejects_max_jobs_over_hard_limit tests/unit/test_main.py::test_claim_max_jobs_overflow_warning_contains_runner_id tests/integration/test_runner_control_api.py::test_claim_rejects_negative_jobs tests/integration/test_runner_control_api.py::test_claim_rejects_zero_max_jobs -v`

Expected: FAIL

- [ ] **Step 11: Implement `max_jobs` hard-limit validation (must execute before any 204 capacity-full return) and warning log in `claim_task`**

```python
if request.max_jobs > config.claim_max_jobs_hard_limit:
    logger.warning(
        "invalid claim payload: max_jobs exceeds hard limit",
        extra={"runner_id": runner_id, "max_jobs": request.max_jobs},
    )
    raise HTTPException(status_code=422, detail="max_jobs exceeds hard limit")
```

- [ ] **Step 12: Add schema bounds in claim request model (`jobs >= 0`, `max_jobs >= 1`)**

```python
class ClaimTaskRequest(BaseModel):
    runner_id: Optional[str] = None
    jobs: int = Field(ge=0)
    max_jobs: int = Field(ge=1)
```

- [ ] **Step 13: Run GREEN for max-jobs-overflow loop and schema-bound tests**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_rejects_max_jobs_over_hard_limit tests/unit/test_main.py::test_claim_max_jobs_overflow_warning_contains_runner_id tests/integration/test_runner_control_api.py::test_claim_rejects_negative_jobs tests/integration/test_runner_control_api.py::test_claim_rejects_zero_max_jobs -v`

Expected: PASS

- [ ] **Step 14: Verify no body-less claim calls remain in touched tests**

Run: `rg '/claim"' backend/tests/integration/test_runner_control_api.py backend/tests/unit/test_main.py -n`

Expected: every matched claim call includes a JSON payload with `jobs` and `max_jobs` (manual inspection of matches)

- [ ] **Step 15: Run GREEN for updated claim tests and helper smoke test**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py::test_claim_assigns_pending_task_and_returns_lease_token tests/integration/test_runner_control_api.py::test_claim_rejects_jobs_greater_than_max_jobs tests/integration/test_runner_control_api.py::test_claim_rejects_max_jobs_over_hard_limit tests/integration/test_runner_control_api.py::test_claim_rejects_negative_jobs tests/integration/test_runner_control_api.py::test_claim_rejects_zero_max_jobs tests/unit/test_main.py::test_runner_helpers_can_create_and_claim_task tests/unit/test_main.py::test_claim_invalid_jobs_warning_contains_runner_id tests/unit/test_main.py::test_claim_max_jobs_overflow_warning_contains_runner_id -v`

Expected: PASS

- [ ] **Step 16: Commit task changes**

```bash
git add backend/app/main.py backend/tests/integration/test_runner_control_api.py backend/tests/unit/test_main.py
git commit -m "feat(runner-api): validate claim capacity payload and migrate claim callsites"
```

## Chunk 2: Runner Worker Concurrency and Heartbeat Decoupling

### Task 4: Send runner capacity in claim payload and enforce local capacity gate (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Modify: `backend/runner/__main__.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_worker_claim_payload_includes_jobs_and_max_jobs`**

```python
@pytest.mark.asyncio
async def test_worker_claim_payload_includes_jobs_and_max_jobs():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None, max_jobs=4)
    await worker.run_once()
    assert client.claims[0]["jobs"] == 0
    assert client.claims[0]["max_jobs"] == 4
```

- [ ] **Step 2: Write failing test `test_worker_skips_claim_when_local_capacity_full`**

```python
@pytest.mark.asyncio
async def test_worker_skips_claim_when_local_capacity_full():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None, max_jobs=1)
    worker._inflight_tasks = {asyncio.create_task(asyncio.sleep(0.2))}
    try:
        await worker.run_once()
        assert client.claims == []
    finally:
        for task in worker._inflight_tasks:
            task.cancel()
```

- [ ] **Step 3: Run RED for worker capacity payload tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_claim_payload_includes_jobs_and_max_jobs tests/unit/runner/test_worker.py::test_worker_skips_claim_when_local_capacity_full -v`

Expected: FAIL

- [ ] **Step 4: Implement minimal inflight-aware claim payload and local gate**

```python
class RunnerWorker:
    def __init__(..., max_jobs: int = 1, ...):
        # Capacity state for local slot control
        self._max_jobs = max_jobs
        self._inflight_tasks: set[asyncio.Task] = set()

    async def run_once(self) -> bool:
        # Reap first so jobs reflects live inflight tasks
        self._reap_done_tasks()
        jobs = len(self._inflight_tasks)
        if jobs >= self._max_jobs:
            # Local capacity full: skip claim
            return False
        claimed = await self._client.claim(
            {"runner_id": self._runner_id, "jobs": jobs, "max_jobs": self._max_jobs}
        )
        ...
```

- [ ] **Step 5: Wire `max_jobs` in runner bootstrap**

```python
worker = RunnerWorker(
    ...,
    max_jobs=config.max_jobs,
)
```

- Edit `backend/runner/__main__.py` specifically so runner bootstrap always passes `max_jobs=config.max_jobs`.

- [ ] **Step 6: Run GREEN for capacity payload tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_claim_payload_includes_jobs_and_max_jobs tests/unit/runner/test_worker.py::test_worker_skips_claim_when_local_capacity_full -v`

Expected: PASS

- [ ] **Step 7: Commit task changes**

```bash
git add backend/runner/worker.py backend/runner/__main__.py backend/tests/unit/runner/test_worker.py
git commit -m "feat(runner): send claim capacity payload and enforce local slot gate"
```

### Task 5: Execute claimed tasks concurrently up to max_jobs (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_worker_fills_multiple_slots_via_repeated_single_claims`**

```python
@pytest.mark.asyncio
async def test_worker_fills_multiple_slots_via_repeated_single_claims():
    tasks = [
        {"id": 1, "lease_token": "l1", "crate_name": "a", "version": "1.0.0"},
        {"id": 2, "lease_token": "l2", "crate_name": "b", "version": "1.0.0"},
        None,
    ]
    client = SequenceClaimClient(tasks)
    executed = []

    class SlowExecutor:
        async def execute_claimed_task(self, claimed):
            executed.append(claimed["id"])
            await asyncio.sleep(0.05)

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=SlowExecutor(), max_jobs=2)
    did_work = await worker.run_once()
    deadline = asyncio.get_running_loop().time() + 1.0
    while len(executed) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert did_work is True
    assert sorted(executed) == [1, 2]
```

- [ ] **Step 2: Replace failing legacy test `test_worker_reports_executor_exception` with detached-model test `test_worker_executor_failure_isolated_from_run_once`**

```python
@pytest.mark.asyncio
async def test_worker_executor_failure_isolated_from_run_once(caplog):
    caplog.set_level("ERROR")
    client = FakeClient(claimed_task={"id": 9, "lease_token": "lease-9", "crate_name": "foo", "version": "1.0.0"})

    class BrokenExecutor:
        async def execute_claimed_task(self, _):
            raise RuntimeError("boom")

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=BrokenExecutor(), max_jobs=1)
    did_work = await worker.run_once()
    assert did_work is True
    deadline = asyncio.get_running_loop().time() + 1.0
    while not any("runner executor failed" in r.message.lower() for r in caplog.records) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    assert any("runner executor failed" in r.message.lower() for r in caplog.records)
```

- [ ] **Step 3: Update failing traceback test for detached execution (`test_worker_executor_failure_logs_traceback`)**

```python
@pytest.mark.asyncio
async def test_worker_executor_failure_logs_traceback(caplog):
    ...
    did_work = await worker.run_once()
    assert did_work is True
    deadline = asyncio.get_running_loop().time() + 1.0
    while not any("runner executor failed" in r.message.lower() for r in caplog.records) and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.01)
    record = next(r for r in caplog.records if "runner executor failed" in r.message.lower())
    assert record.exc_info is not None
```

- [ ] **Step 4: Run RED for detached-concurrency worker tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_fills_multiple_slots_via_repeated_single_claims tests/unit/runner/test_worker.py::test_worker_executor_failure_isolated_from_run_once tests/unit/runner/test_worker.py::test_worker_executor_failure_logs_traceback -v`

Expected: FAIL

- [ ] **Step 5: Implement slot-fill loop, detached execution tasks, and exception isolation**

```python
while len(self._inflight_tasks) < self._max_jobs:
    claimed = await self._client.claim({...})
    if claimed is None:
        break
    task = asyncio.create_task(self._execute_claimed_task_safe(claimed))
    self._inflight_tasks.add(task)
    task.add_done_callback(self._inflight_tasks.discard)

async def _execute_claimed_task_safe(self, claimed):
    try:
        await self._executor.execute_claimed_task(claimed)
    except Exception:
        logger.exception(...)
        # Do not re-raise: failure is isolated from run_once loop
```

- [ ] **Step 6: Run GREEN for detached-concurrency worker tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_worker_fills_multiple_slots_via_repeated_single_claims tests/unit/runner/test_worker.py::test_worker_executor_failure_isolated_from_run_once tests/unit/runner/test_worker.py::test_worker_executor_failure_logs_traceback -v`

Expected: PASS

- [ ] **Step 7: Commit task changes**

```bash
git add backend/runner/worker.py backend/tests/unit/runner/test_worker.py
git commit -m "feat(runner): fill available slots by repeated single-task claims"
```

### Task 6: Make heartbeat process-lifetime and independent from task lifecycle (TDD)

**Files:**
- Modify: `backend/tests/unit/runner/test_worker.py`
- Modify: `backend/runner/worker.py`
- Test: `backend/tests/unit/runner/test_worker.py`

- [ ] **Step 1: Write failing test `test_run_forever_uses_single_heartbeat_thread_lifecycle`**

```python
@pytest.mark.asyncio
async def test_run_forever_uses_single_heartbeat_thread_lifecycle(monkeypatch):
    start_calls = []
    stop_calls = []
    worker = RunnerWorker(...)
    monkeypatch.setattr(worker, "_start_heartbeat_background", lambda: start_calls.append("start"))
    monkeypatch.setattr(worker, "_stop_heartbeat_background", lambda: stop_calls.append("stop"))
    ...
    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker.run_forever(0.01)
    assert start_calls == ["start"]
    assert stop_calls == ["stop"]
```

- [ ] **Step 2: Write failing test `test_run_once_does_not_start_task_scoped_heartbeat_thread`**

```python
@pytest.mark.asyncio
async def test_run_once_does_not_start_task_scoped_heartbeat_thread(monkeypatch):
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None, max_jobs=1)
    calls = []
    monkeypatch.setattr(worker, "_start_heartbeat_thread", lambda *_args, **_kwargs: calls.append("start"))
    await worker.run_once()
    assert calls == []
```

- [ ] **Step 3: Replace failing legacy test with deterministic process-lifecycle heartbeat test `test_heartbeat_continues_while_executor_blocks_main_event_loop`**

```python
@pytest.mark.asyncio
async def test_heartbeat_continues_while_executor_blocks_main_event_loop():
    task = {
        "id": 88,
        "lease_token": "lease-88",
        "crate_name": "foo",
        "version": "1.0.0",
    }
    client = SequenceClaimClient([task, None, None])
    heartbeat_client = FakeClient()

    class BlockingExecutor:
        async def execute_claimed_task(self, _):
            time.sleep(0.35)

    worker = RunnerWorker(
        client=client,
        runner_id="runner-1",
        executor=BlockingExecutor(),
        max_jobs=1,
        heartbeat_interval_seconds=0.1,
        heartbeat_client_factory=lambda: heartbeat_client,
    )

    runner_task = asyncio.create_task(worker.run_forever(0.02))
    deadline = asyncio.get_running_loop().time() + 2.0
    while len(heartbeat_client.heartbeats) < 2 and asyncio.get_running_loop().time() < deadline:
        await asyncio.sleep(0.02)
    runner_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await runner_task

    assert len(heartbeat_client.heartbeats) >= 2
```

- [ ] **Step 4: Write failing test `test_run_forever_shutdown_waits_for_inflight_tasks_before_exit`**

```python
@pytest.mark.asyncio
async def test_run_forever_shutdown_waits_for_inflight_tasks_before_exit():
    started = asyncio.Event()
    finished = asyncio.Event()

    class SlowExecutor:
        async def execute_claimed_task(self, _):
            started.set()
            await asyncio.sleep(0.2)
            finished.set()

    client = SequenceClaimClient([
        {"id": 1, "lease_token": "lease-1", "crate_name": "foo", "version": "1.0.0"},
        None,
    ])
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=SlowExecutor(), max_jobs=1)

    run_task = asyncio.create_task(worker.run_forever(0.01))
    await started.wait()
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert finished.is_set()
```

- [ ] **Step 5: Run RED for heartbeat lifecycle tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_run_forever_uses_single_heartbeat_thread_lifecycle tests/unit/runner/test_worker.py::test_run_once_does_not_start_task_scoped_heartbeat_thread tests/unit/runner/test_worker.py::test_heartbeat_continues_while_executor_blocks_main_event_loop tests/unit/runner/test_worker.py::test_run_forever_shutdown_waits_for_inflight_tasks_before_exit -v`

Expected: FAIL

- [ ] **Step 6: Implement single process-lifecycle heartbeat and remove per-task heartbeat lifecycle from `run_once`**

```python
async def run_forever(self, poll_interval_seconds: float) -> None:
    self._start_heartbeat_background()
    try:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
    finally:
        self._stop_heartbeat_background()

# run_once no longer starts/stops/joins heartbeat threads
# remove task-scoped `_start_heartbeat_thread`/`join` lifecycle from run_once
```

- [ ] **Step 7: Implement shutdown inflight-task settle policy in `run_forever` finalization**

```python
finally:
    try:
        if self._inflight_tasks:
            try:
                await asyncio.shield(
                    asyncio.wait(self._inflight_tasks, timeout=5.0)
                )
            finally:
                pending = {t for t in self._inflight_tasks if not t.done()}
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)
    finally:
        self._stop_heartbeat_background()
```

- Use `asyncio.shield(...)` so shutdown settling is not preempted by outer cancellation.
- Keep heartbeat active until inflight shutdown settle/cancel logic completes.

- [ ] **Step 8: Run GREEN for heartbeat lifecycle tests**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py::test_run_forever_uses_single_heartbeat_thread_lifecycle tests/unit/runner/test_worker.py::test_run_once_does_not_start_task_scoped_heartbeat_thread tests/unit/runner/test_worker.py::test_heartbeat_continues_while_executor_blocks_main_event_loop tests/unit/runner/test_worker.py::test_run_forever_shutdown_waits_for_inflight_tasks_before_exit -v`

Expected: PASS

- Confirm this GREEN set supersedes old assumptions from legacy tests (`run_once` no longer raises executor exceptions; heartbeat no longer task-scoped).

- [ ] **Step 9: Run full worker unit file and commit**

Run: `uv run --directory backend pytest tests/unit/runner/test_worker.py -v`

Expected: PASS

```bash
git add backend/runner/worker.py backend/tests/unit/runner/test_worker.py
git commit -m "refactor(runner): decouple heartbeat lifecycle from task execution"
```

## Chunk 3: Client Assertions, Regression Coverage, and Verification

### Task 7: Tighten runner client claim request contract test

**Files:**
- Modify: `backend/tests/unit/runner/test_runner_client.py`
- Test: `backend/tests/unit/runner/test_runner_client.py`
- Ensure test module imports `json` for request payload assertion.

- [ ] **Step 1: Update test `test_claim_returns_none_on_204` to assert payload**

```python
async def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/api/runners/runner-1/claim"
    payload = json.loads(request.content.decode("utf-8"))
    assert payload == {"runner_id": "runner-1", "jobs": 0, "max_jobs": 3}
    return httpx.Response(status_code=204, request=request)

import json

result = await client.claim({"runner_id": "runner-1", "jobs": 0, "max_jobs": 3})
```

- [ ] **Step 2: Run claim contract test**

Run: `uv run --directory backend pytest tests/unit/runner/test_runner_client.py::test_claim_returns_none_on_204 -v`

Expected: PASS

- [ ] **Step 3: Run full runner client unit file**

Run: `uv run --directory backend pytest tests/unit/runner/test_runner_client.py -v`

Expected: PASS

- [ ] **Step 4: Commit task changes**

```bash
git add backend/tests/unit/runner/test_runner_client.py
git commit -m "test(runner-client): assert claim payload includes jobs and max_jobs"
```

### Task 8: Run backend claim/worker focused regression suite

**Files:**
- Verify: `backend/app/main.py`
- Verify: `backend/app/config.py`
- Verify: `backend/runner/worker.py`
- Verify: `backend/tests/integration/test_runner_control_api.py`
- Verify: `backend/tests/unit/test_main.py`
- Verify: `backend/tests/unit/test_config.py`
- Verify: `backend/tests/unit/runner/test_worker.py`
- Verify: `backend/tests/unit/runner/test_runner_client.py`

- [ ] **Step 1: Run integration claim suite**

Run: `uv run --directory backend pytest tests/integration/test_runner_control_api.py -k "claim or heartbeat_extends_task_lease" -v`

Expected: PASS

- [ ] **Step 2: Run unit suites touched by protocol update**

Run: `uv run --directory backend pytest tests/unit/test_main.py tests/unit/test_config.py tests/unit/runner/test_worker.py tests/unit/runner/test_runner_client.py -v`

Expected: PASS

- [ ] **Step 3: Run focused grep for old no-body claim calls in tests**

Run: `rg "/claim[\"']" backend/tests -n`

Expected: matches may exist; manually verify each claim POST includes JSON payload with `jobs` and `max_jobs`.

- [ ] **Step 4: Do not commit in this task; defer any final commit to Task 9**

Run: `git status --short`

Expected: if files changed from regression fixes, leave them for Task 9 Step 5 final integration commit.

### Task 9: Final verification and handoff checks

**Files:**
- Verify only (no required code changes)

- [ ] **Step 1: Run full backend unit test suite**

Run: `uv run --directory backend pytest tests/unit -v`

Expected: PASS (or only pre-existing unrelated failures)

- [ ] **Step 2: Run full backend integration test suite**

Run: `uv run --directory backend pytest tests/integration -v`

Expected: PASS (or only pre-existing unrelated failures)

- [ ] **Step 3: Verify no sensitive auth token logging introduced in touched backend files**

Run: `rg 'Authorization|Bearer|token_hash|token_salt' backend/app/main.py backend/runner/worker.py`

Expected: no newly added token value logging statements

- [ ] **Step 4: Optional docs alignment check for distributed config sample**

Run: `uv run --directory backend pytest tests/unit/test_config.py::test_config_loads_from_file -v`

Expected: PASS

- [ ] **Step 5: Create final integration commit if any final polish edits were made**

```bash
# if final polish touched relevant files:
git add backend/tests/integration/test_runner_control_api.py backend/tests/unit/test_main.py backend/tests/unit/test_config.py backend/tests/unit/runner/test_worker.py backend/tests/unit/runner/test_runner_client.py backend/app/main.py backend/app/config.py backend/runner/worker.py backend/runner/__main__.py config.toml.example
git commit -m "feat(runner): support self-reported claim capacity with decoupled heartbeat"
```
