import pytest

from app.runner.worker import RunnerWorker


class FakeClient:
    def __init__(self, claimed_task=None):
        self.claimed_task = claimed_task
        self.heartbeats = []
        self.claims = []
        self.events = []

    async def heartbeat(self, payload):
        self.heartbeats.append(payload)
        return {"ok": True}

    async def claim(self, payload):
        self.claims.append(payload)
        return self.claimed_task

    async def send_event(self, task_id, payload):
        self.events.append((task_id, payload))
        return {"applied": True}


@pytest.mark.asyncio
async def test_worker_heartbeats_when_idle():
    client = FakeClient(claimed_task=None)
    worker = RunnerWorker(client=client, runner_id="runner-1")

    did_work = await worker.run_once()

    assert did_work is False
    assert len(client.heartbeats) == 1
    assert len(client.claims) == 1
    assert client.events == []


@pytest.mark.asyncio
async def test_worker_claims_and_reports_started_completed():
    task = {"id": 42, "lease_token": "lease-42", "command": ["/bin/true"]}
    client = FakeClient(claimed_task=task)
    executed = []

    async def executor(claimed_task):
        executed.append(claimed_task["id"])

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=executor)

    did_work = await worker.run_once()

    assert did_work is True
    assert executed == [42]
    assert client.events == [
        (42, {"lease_token": "lease-42", "event_seq": 1, "event_type": "started"}),
        (
            42,
            {"lease_token": "lease-42", "event_seq": 2, "event_type": "completed"},
        ),
    ]


@pytest.mark.asyncio
async def test_worker_reports_failed_on_executor_exception():
    task = {"id": 9, "lease_token": "lease-9", "command": ["/bin/false"]}
    client = FakeClient(claimed_task=task)

    async def broken_executor(_):
        raise RuntimeError("boom")

    worker = RunnerWorker(client=client, runner_id="runner-1", executor=broken_executor)

    did_work = await worker.run_once()

    assert did_work is True
    assert client.events == [
        (9, {"lease_token": "lease-9", "event_seq": 1, "event_type": "started"}),
        (9, {"lease_token": "lease-9", "event_seq": 2, "event_type": "failed"}),
    ]
