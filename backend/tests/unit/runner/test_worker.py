import asyncio
import threading
import time

import pytest
from runner.worker import RunnerWorker


class FakeClient:
    def __init__(self, claimed_task=None):
        self.claimed_task = claimed_task
        self.heartbeats = []
        self.claims = []
        self.events = []
        self.metrics = []

    async def heartbeat(self, payload):
        self.heartbeats.append(payload)
        return {"ok": True}

    async def claim(self, payload):
        self.claims.append(payload)
        return self.claimed_task

    async def send_event(self, task_id, payload):
        self.events.append((task_id, payload))
        return {"applied": True}

    async def send_metrics(self, payload):
        self.metrics.append(payload)
        return {"success": True}

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_worker_sends_metrics_and_claims_when_idle():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    did_work = await worker.run_once()

    assert did_work is False
    assert len(client.heartbeats) == 0
    assert len(client.claims) == 1
    assert len(client.metrics) == 1
    assert client.events == []


@pytest.mark.asyncio
async def test_worker_claim_payload_includes_jobs_and_max_jobs():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(
        client=client, runner_id="runner-1", executor=None, max_jobs=3
    )

    did_work = await worker.run_once()

    assert did_work is False
    assert len(client.claims) == 1
    assert client.claims[0] == {"runner_id": "runner-1", "jobs": 0, "max_jobs": 3}


@pytest.mark.asyncio
async def test_worker_skips_claim_when_local_capacity_full(monkeypatch):
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(
        client=client, runner_id="runner-1", executor=None, max_jobs=1
    )

    monkeypatch.setattr(worker, "_current_jobs", lambda: 1)

    did_work = await worker.run_once()

    assert did_work is False
    assert client.claims == []


@pytest.mark.asyncio
async def test_worker_claims_and_executes_task():
    task = {
        "id": 42,
        "lease_token": "lease-42",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }
    client = FakeClient(claimed_task=task)
    executed = []

    class FakeExecutor:
        async def execute_claimed_task(self, claimed_task):
            executed.append(claimed_task["id"])

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=FakeExecutor())

    did_work = await worker.run_once()

    assert did_work is True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while worker._current_jobs() > 0 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert executed == [42]
    assert len(client.metrics) == 1
    assert client.events == []


@pytest.mark.asyncio
async def test_worker_fills_multiple_slots_via_repeated_single_claims():
    tasks_to_claim = [
        {
            "id": 101,
            "lease_token": "lease-101",
            "crate_name": "foo",
            "crate_version": "1.0.0",
        },
        {
            "id": 102,
            "lease_token": "lease-102",
            "crate_name": "bar",
            "crate_version": "2.0.0",
        },
    ]

    class SequentialClaimClient(FakeClient):
        async def claim(self, payload):
            self.claims.append(payload)
            if tasks_to_claim:
                return tasks_to_claim.pop(0)
            return None

    client = SequentialClaimClient()
    execution_started = []
    release_tasks = asyncio.Event()

    class BlockingExecutor:
        async def execute_claimed_task(self, claimed_task):
            execution_started.append(claimed_task["id"])
            await release_tasks.wait()

    worker = RunnerWorker(
        client=client,
        runner_id="runner-1",
        executor=BlockingExecutor(),
        max_jobs=2,
    )

    did_work = await asyncio.wait_for(worker.run_once(), timeout=0.2)

    assert did_work is True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while len(execution_started) < 2 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert execution_started == [101, 102]
    assert client.claims == [
        {"runner_id": "runner-1", "jobs": 0, "max_jobs": 2},
        {"runner_id": "runner-1", "jobs": 1, "max_jobs": 2},
    ]

    release_tasks.set()
    while worker._current_jobs() > 0 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert worker._current_jobs() == 0


@pytest.mark.asyncio
async def test_worker_executor_failure_isolated_from_run_once():
    task = {
        "id": 9,
        "lease_token": "lease-9",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }
    client = FakeClient(claimed_task=task)

    class BrokenExecutor:
        async def execute_claimed_task(self, _):
            raise RuntimeError("boom")

    worker = RunnerWorker(
        client=client, runner_id="runner-1", executor=BrokenExecutor()
    )

    did_work = await worker.run_once()

    assert did_work is True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while worker._current_jobs() > 0 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert worker._current_jobs() == 0
    assert len(client.metrics) == 1
    assert client.events == []


@pytest.mark.asyncio
async def test_worker_metrics_failure_does_not_break_run_once():
    client = FakeClient(claimed_task=None)

    async def broken_send_metrics(_payload):
        raise RuntimeError("metrics down")

    client.send_metrics = broken_send_metrics
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    did_work = await worker.run_once()

    assert did_work is False


@pytest.mark.asyncio
async def test_worker_metrics_warning_contains_runner_id(caplog):
    caplog.set_level("WARNING")
    client = FakeClient(claimed_task=None)

    async def broken_send_metrics(_payload):
        raise RuntimeError("metrics down")

    client.send_metrics = broken_send_metrics
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    did_work = await worker.run_once()

    assert did_work is False
    record = next(
        r
        for r in caplog.records
        if "failed to send runner metrics" in r.message.lower()
    )
    assert record.runner_id == "runner-1"


@pytest.mark.asyncio
async def test_worker_claim_transport_failure_logs_warning_with_runner_id(caplog):
    caplog.set_level("WARNING")

    class BrokenClaimClient(FakeClient):
        async def claim(self, payload):
            self.claims.append(payload)
            raise RuntimeError("claim down")

    client = BrokenClaimClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    with pytest.raises(RuntimeError, match="claim down"):
        await worker.run_once()

    record = next(
        r for r in caplog.records if "runner claim request failed" in r.message.lower()
    )
    assert record.runner_id == "runner-1"


@pytest.mark.asyncio
async def test_worker_executor_failure_logs_traceback(caplog):
    caplog.set_level("ERROR")
    task = {
        "id": 22,
        "lease_token": "lease-22",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }
    client = FakeClient(claimed_task=task)

    class BrokenExecutor:
        async def execute_claimed_task(self, _):
            raise RuntimeError("executor boom")

    worker = RunnerWorker(
        client=client, runner_id="runner-1", executor=BrokenExecutor()
    )

    did_work = await worker.run_once()

    assert did_work is True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    record = None
    while loop.time() < deadline:
        record = next(
            (
                r
                for r in caplog.records
                if "runner executor failed" in r.message.lower()
            ),
            None,
        )
        if record is not None:
            break
        await asyncio.sleep(0)

    assert record is not None
    assert record.exc_info is not None
    assert record.runner_id == "runner-1"
    assert record.task_id == 22
    assert record.crate_name == "foo"


@pytest.mark.asyncio
async def test_worker_run_forever_uses_sleep_interval(monkeypatch):
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(
        client=client, runner_id="runner-1", executor=None, metrics_interval_seconds=10
    )

    sleep_calls = []

    async def fake_sleep(duration):
        sleep_calls.append(duration)
        raise RuntimeError("stop-loop")

    monkeypatch.setattr("runner.worker.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker.run_forever(3.5)

    assert sleep_calls == [3.5]


@pytest.mark.asyncio
async def test_worker_metrics_payload_uses_disk_percent():
    """Regression test: metrics payload must use disk_percent to match backend API schema."""
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    await worker.run_once()

    assert len(client.metrics) == 1
    payload = client.metrics[0]
    assert "disk_percent" in payload
    assert "disk_usage_percent" not in payload
    assert isinstance(payload["cpu_percent"], float)
    assert isinstance(payload["memory_percent"], float)
    assert isinstance(payload["disk_percent"], float)
    assert isinstance(payload["active_tasks"], int)


@pytest.mark.asyncio
async def test_run_forever_uses_single_heartbeat_thread_lifecycle(monkeypatch):
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    started = []
    stopped = []
    run_once_calls = []

    def fake_start_heartbeat_background():
        started.append(True)

    def fake_stop_heartbeat_background():
        stopped.append(True)

    async def fake_run_once():
        run_once_calls.append(True)
        return False

    async def fake_sleep(_duration):
        raise RuntimeError("stop-loop")

    monkeypatch.setattr(
        worker, "_start_heartbeat_background", fake_start_heartbeat_background
    )
    monkeypatch.setattr(
        worker, "_stop_heartbeat_background", fake_stop_heartbeat_background
    )
    monkeypatch.setattr(worker, "run_once", fake_run_once)
    monkeypatch.setattr("runner.worker.asyncio.sleep", fake_sleep)

    with pytest.raises(RuntimeError, match="stop-loop"):
        await worker.run_forever(1.0)

    assert len(run_once_calls) == 1
    assert len(started) == 1
    assert len(stopped) == 1


def test_stop_heartbeat_background_keeps_references_when_join_times_out(caplog):
    caplog.set_level("WARNING")
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    class StubbornThread:
        def __init__(self):
            self.join_timeouts = []

        def join(self, timeout=None):
            self.join_timeouts.append(timeout)

        def is_alive(self):
            return True

    stop_event = threading.Event()
    stubborn_thread = StubbornThread()
    worker._heartbeat_stop_event = stop_event
    worker._heartbeat_thread = stubborn_thread

    worker._stop_heartbeat_background()

    assert stop_event.is_set() is True
    assert stubborn_thread.join_timeouts == [5.0]
    assert worker._heartbeat_stop_event is stop_event
    assert worker._heartbeat_thread is stubborn_thread
    assert any(
        "heartbeat thread did not stop within timeout" in record.message.lower()
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_run_once_does_not_start_task_scoped_heartbeat_thread(monkeypatch):
    task = {
        "id": 77,
        "lease_token": "lease-77",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }
    client = FakeClient(claimed_task=task)
    executed = []

    class FakeExecutor:
        async def execute_claimed_task(self, claimed_task):
            executed.append(claimed_task["id"])

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=FakeExecutor())

    if hasattr(worker, "_start_heartbeat_thread"):
        monkeypatch.setattr(
            worker,
            "_start_heartbeat_thread",
            lambda _stop_event: (_ for _ in ()).throw(
                AssertionError("task-scoped heartbeat should not be started")
            ),
        )

    did_work = await worker.run_once()

    assert did_work is True
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while worker._current_jobs() > 0 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert executed == [77]


@pytest.mark.asyncio
async def test_heartbeat_continues_while_executor_blocks_main_event_loop():
    task = {
        "id": 88,
        "lease_token": "lease-88",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }

    class SingleTaskClient(FakeClient):
        def __init__(self):
            super().__init__(claimed_task=None)
            self._claimed = False

        async def claim(self, payload):
            self.claims.append(payload)
            if not self._claimed:
                self._claimed = True
                return task
            return None

    class ObservedHeartbeatClient(FakeClient):
        def __init__(self, started_event, finished_event, seen_event):
            super().__init__(claimed_task=None)
            self._started_event = started_event
            self._finished_event = finished_event
            self._seen_event = seen_event
            self._during_block_count = 0

        async def heartbeat(self, payload):
            self.heartbeats.append(payload)
            if self._started_event.is_set() and not self._finished_event.is_set():
                self._during_block_count += 1
                if self._during_block_count >= 1:
                    self._seen_event.set()
            return {"ok": True}

    started_event = threading.Event()
    finished_event = threading.Event()
    seen_event = threading.Event()
    client = SingleTaskClient()
    heartbeat_client = ObservedHeartbeatClient(
        started_event, finished_event, seen_event
    )

    class SlowExecutor:
        async def execute_claimed_task(self, _):
            started_event.set()
            time.sleep(0.8)
            finished_event.set()

    worker = RunnerWorker(
        client=client,
        runner_id="runner-1",
        executor=SlowExecutor(),
        heartbeat_interval_seconds=0.02,
        heartbeat_client_factory=lambda: heartbeat_client,
    )

    run_task = asyncio.create_task(worker.run_forever(10.0))

    await asyncio.to_thread(started_event.wait, 1.0)
    observed = await asyncio.to_thread(seen_event.wait, 2.5)
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task

    assert observed is True
    assert len(heartbeat_client.heartbeats) >= 2


@pytest.mark.asyncio
async def test_run_forever_shutdown_waits_for_inflight_tasks_before_exit(monkeypatch):
    task = {
        "id": 99,
        "lease_token": "lease-99",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }

    class SingleTaskClient(FakeClient):
        def __init__(self):
            super().__init__(claimed_task=None)
            self._claimed = False

        async def claim(self, payload):
            self.claims.append(payload)
            if not self._claimed:
                self._claimed = True
                return task
            return None

    client = SingleTaskClient()
    release_executor = asyncio.Event()
    cancel_seen = asyncio.Event()

    class CancellationResistantExecutor:
        async def execute_claimed_task(self, _):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancel_seen.set()
                await release_executor.wait()

    worker = RunnerWorker(
        client=client,
        runner_id="runner-1",
        executor=CancellationResistantExecutor(),
    )

    lifecycle = []
    monkeypatch.setattr(
        worker, "_start_heartbeat_background", lambda: lifecycle.append("start")
    )
    monkeypatch.setattr(
        worker, "_stop_heartbeat_background", lambda: lifecycle.append("stop")
    )

    original_wait = asyncio.wait

    async def fast_wait(fs, timeout=None, return_when=asyncio.ALL_COMPLETED):
        effective_timeout = timeout
        if timeout == 5.0:
            effective_timeout = 0.05
        return await original_wait(
            fs, timeout=effective_timeout, return_when=return_when
        )

    monkeypatch.setattr("runner.worker.asyncio.wait", fast_wait)

    run_task = asyncio.create_task(worker.run_forever(10.0))

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1.0
    while worker._current_jobs() < 1 and loop.time() < deadline:
        await asyncio.sleep(0)

    assert worker._current_jobs() == 1

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=0.5)

    assert cancel_seen.is_set() is True
    assert lifecycle == ["start", "stop"]

    release_executor.set()
    settle_deadline = loop.time() + 0.5
    while worker._current_jobs() > 0 and loop.time() < settle_deadline:
        await asyncio.sleep(0)

    assert worker._current_jobs() == 0
