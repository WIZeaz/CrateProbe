# Runner Self-Reported Capacity and Heartbeat Decoupling Design

## Goal

Enable each runner to claim up to its own `max_jobs` capacity by reporting runtime state (`jobs`, `max_jobs`) on every claim request, while decoupling heartbeat from task execution so a stuck or slow task cannot block lease keepalive.

## Confirmed Decisions

- Capacity source of truth is the runner claim payload (`jobs`, `max_jobs`), not backend-derived runtime counts.
- Backend database does not persist runner runtime concurrency details.
- Claim remains single-task per request.
- Protocol upgrade is direct (no backward compatibility layer for old claim payloads).
- Downscaling behavior is soft-limiting: if `jobs >= max_jobs`, backend rejects further claims with `204`, and existing tasks continue naturally.

## Scope

### In Scope

- Backend claim API contract update (`/api/runners/{runner_id}/claim`) to require `jobs` and `max_jobs`.
- Backend claim gate logic based on runner-reported values.
- Runner worker redesign for concurrent execution up to `max_jobs`.
- Runner heartbeat lifecycle redesign to be process-level, not per-task.
- Unit/integration test updates for new claim contract and concurrency behavior.

### Out of Scope

- Admin-side dynamic backend override of runner concurrency.
- Backend persistence of runner runtime load (`jobs`, `active_slots`, etc.).
- Batch claim endpoint (single-task claim remains).

## Current State Summary

- Backend claim endpoint currently accepts no claim-body capacity data and claims one pending task immediately when available.
- Runner worker currently executes one claimed task at a time.
- Heartbeat is partially decoupled (thread exists), but lifecycle is tied to an individual task execution window, not runner process lifetime.

## Proposed Design

## 1) API Contract and Validation

Add a request model for claim:

```json
{
  "runner_id": "runner-1",
  "jobs": 2,
  "max_jobs": 4
}
```

Design notes:

- Path `runner_id` is authoritative.
- Body `runner_id` is optional for readability and ignored by backend in this phase.
- `jobs` is runner-reported current executing task count.
- `max_jobs` is runner-reported capacity for this claim decision (supports runtime updates from runner config reload/restart behavior).
- Validate shape/ranges in API model:
  - `jobs >= 0`
  - `max_jobs >= 1`
  - `jobs <= max_jobs`
  - `max_jobs <= claim_max_jobs_hard_limit` (new backend config guard, default `256`)

Because this is a direct protocol upgrade, claim requests without required fields fail validation (`422`).

## 2) Backend Claim Gate Logic

Within `claim_task` handler:

1. Authenticate runner as today.
2. Read `jobs` and `max_jobs` from request body.
3. If `jobs >= max_jobs`, return `204` immediately.
4. Else call existing `db.claim_pending_task(runner_id, lease_ttl_seconds)`.
5. If DB returns no pending row, return `204`; otherwise return current claim payload.

Important boundary:

- Backend uses runner-reported runtime values for capacity decision.
- Backend task table still stores task lease ownership fields (`runner_id`, `lease_token`, `lease_expires_at`) because they are task protocol state, not runner runtime inventory state.

## 3) Runner Concurrency Model

Runner worker moves from single-task execution to slot-based execution:

- Maintain `inflight: set[asyncio.Task]` for currently executing task coroutines.
- Main loop behavior:
  1. Reap completed tasks from `inflight`.
  2. Compute `jobs = len(inflight)` and read `max_jobs` from runner config.
  3. If `jobs >= max_jobs`, sleep poll interval.
  4. If capacity available, call claim with `{runner_id, jobs, max_jobs}`.
  5. On claimed task, create execution coroutine, add to `inflight`, and continue filling until full or claim returns no task.
  6. If claim returns `204` (no task) or claim attempt fails, sleep poll interval before the next claim attempt.

Single-request claim is preserved; parallelism is achieved by repeated claims until slots are full.

## 4) Heartbeat/Worker Decoupling

Heartbeat becomes a process-level background loop:

- Start once when runner starts.
- Use dedicated heartbeat client (cloned HTTP client) to isolate failures and avoid transport coupling with worker claim/event traffic.
- Continue sending heartbeat at fixed interval regardless of task execution state.
- Stop only during runner shutdown.
- Requirement: task execution path must remain non-blocking to the main event loop (for example asyncio subprocess APIs or offloading blocking calls to threads).

Effects:

- Task lifecycle no longer controls heartbeat lifetime; this guarantee assumes task execution does not block the event loop.
- Lease extension remains stable while any owned task is running, as long as runner process is alive and heartbeat transport is healthy.

## 5) Error Handling

- Claim transport failure: log warning with `runner_id`; keep loop alive, then sleep poll interval before retry.
- Task execution failure: isolate to task coroutine, preserve current terminal event behavior.
- Heartbeat transport failure: warning and retry in heartbeat loop; does not stop worker loop.
- Shutdown: cancel heartbeat loop and wait for in-flight execution tasks to settle based on existing shutdown policy.
- Invalid claim payload (`jobs > max_jobs`, negative jobs, max_jobs hard-limit overflow): return `422` and log structured warning with `runner_id`.

## 6) Logging and Observability

Add/normalize structured runner logs for:

- claim attempts (`runner_id`, `jobs`, `max_jobs`)
- claim gate result (`admitted` vs `capacity_blocked`)
- in-flight task count changes
- heartbeat loop start/stop/failure

No secrets (token/auth headers) are logged.

## 7) Testing Strategy

### Backend

- Update integration tests in `backend/tests/integration/test_runner_control_api.py`:
  - claim requires `jobs/max_jobs`
  - claim returns `204` when `jobs >= max_jobs` even with pending tasks
  - claim returns task when `jobs < max_jobs` and pending exists

### Runner client

- Update `backend/tests/unit/runner/test_runner_client.py` for new claim payload expectations.

### Runner worker

- Expand `backend/tests/unit/runner/test_worker.py`:
  - worker can run multiple tasks concurrently up to `max_jobs`
  - worker sends claim payload with current `jobs/max_jobs`
  - heartbeat keeps running while tasks execute
  - capacity full path does not claim more tasks until inflight shrinks

## 8) Rollout and Compatibility

- Recommended rollout order:
  1. Cordon/pause active runners (stop new claim traffic).
  2. Deploy backend claim contract changes.
  3. Deploy runner changes (new claim payload + concurrent worker loop).
  4. Uncordon runners.
- Old runners that do not send claim payload receive `422` and stop claiming, which is expected under direct protocol upgrade.
- Rollback guidance:
  - If runner rollout fails, keep runners cordoned and roll back backend claim validation change.
  - After rollback or successful rollout, verify claim success rate is non-zero and `422` errors are not sustained.

## 9) Acceptance Criteria

- Runner claims include `jobs` and `max_jobs` on every request.
- Backend denies claim when `jobs >= max_jobs` without consulting DB runtime counts.
- Runner executes up to configured `max_jobs` concurrently.
- Heartbeat loop is process-level and remains active while tasks are running.
- Updated backend and runner tests pass.

## 10) Risk and Trust Model

- Trust boundary: authenticated runner is authoritative for runtime capacity (`jobs`, `max_jobs`) within request-validation bounds.
- Known risk: buggy runner reports can under-utilize or over-claim capacity.
- Mitigations in this phase:
  - strict claim payload validation (`jobs <= max_jobs`, hard upper bound for `max_jobs`)
  - structured warning logs for invalid payloads
  - post-deploy monitoring of claim `422` rate per `runner_id`
