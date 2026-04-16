import asyncio

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


@pytest.mark.asyncio
async def test_worker_heartbeats_when_idle():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1", executor=None)

    did_work = await worker.run_once()

    assert did_work is False
    assert len(client.heartbeats) == 1
    assert len(client.claims) == 1
    assert len(client.metrics) == 1
    assert client.events == []


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
    assert executed == [42]
    assert len(client.metrics) == 1
    assert client.events == []


@pytest.mark.asyncio
async def test_worker_reports_executor_exception():
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

    with pytest.raises(RuntimeError, match="boom"):
        await worker.run_once()

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
async def test_worker_sends_heartbeats_during_task_execution():
    """Regression test: heartbeats must continue while a task is executing to keep lease alive."""
    task = {
        "id": 77,
        "lease_token": "lease-77",
        "crate_name": "foo",
        "crate_version": "1.0.0",
    }
    client = FakeClient(claimed_task=task)

    class SlowExecutor:
        async def execute_claimed_task(self, _):
            await asyncio.sleep(0.15)

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=SlowExecutor())

    did_work = await worker.run_once()

    assert did_work is True
    # Should see at least 2 heartbeats: one before claim and one during execution
    assert len(client.heartbeats) >= 2
