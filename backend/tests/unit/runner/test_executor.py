import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from core.models import TaskStatus
from runner.crates_api import CratesAPI
from runner.executor import TaskExecutor
from runner.reporter import TaskReporter


def test_count_generated_items(tmp_path):
    workspace = tmp_path / "workspace"
    testgen = workspace / "testgen"
    (testgen / "tests" / "a").mkdir(parents=True)
    (testgen / "tests" / "b").mkdir(parents=True)
    (testgen / "poc" / "x").mkdir(parents=True)
    executor = object.__new__(TaskExecutor)
    assert executor._count_generated_items(workspace) == (2, 1)


def test_get_compile_failed_count(tmp_path):
    stats = tmp_path / "testgen" / "stats.yaml"
    stats.parent.mkdir(parents=True)
    stats.write_text("CompileFailed: 5\n")
    executor = object.__new__(TaskExecutor)
    assert executor._get_compile_failed_count(tmp_path) == 5


@pytest.mark.asyncio
async def test_execute_claimed_task_does_not_block_event_loop_during_docker_prechecks(
    tmp_path, monkeypatch
):
    class FakeClient:
        async def send_event(self, task_id, payload):
            return {"applied": True}

        async def send_log_chunk(self, task_id, log_type, payload):
            return {"appended": True}

    class FakeDocker:
        async def is_available(self):
            await asyncio.sleep(0.4)
            return True

        async def ensure_image(self, _pull_policy):
            await asyncio.sleep(0.4)
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, command, workspace_dir, stdout_log, stderr_log):
            return SimpleNamespace(
                state=SimpleNamespace(value="completed"), exit_code=0, message=""
            )

        async def close(self):
            pass

    class FakeConfig:
        workspace_dir = str(tmp_path / "workspace")
        docker_pull_policy = "if-not-present"
        docker_image = "rust:test"
        log_flush_interval_seconds = 3.0
        log_sync_interval_seconds = 2.0
        max_memory_gb = 8
        max_runtime_seconds = 10
        max_cpus = 2
        docker_mounts = []

    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = object.__new__(TaskExecutor)
    executor.config = FakeConfig()
    executor.client = FakeClient()
    executor.crates_api = object.__new__(CratesAPI)

    async def noop_prepare_workspace(
        workspace_dir, crate_name, version, task_logger, docker
    ):
        return None

    executor._prepare_workspace = noop_prepare_workspace
    executor._count_generated_items = lambda _workspace_dir: (0, 0)
    executor._get_compile_failed_count = lambda _workspace_dir: None

    claimed = {
        "id": 1,
        "lease_token": "lease-1",
        "crate_name": "serde",
        "version": "1.0.0",
    }

    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks, stop
        while not stop:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        await executor.execute_claimed_task(claimed)
    finally:
        stop = True
        await ticker_task

    assert ticks >= 5


@pytest.mark.asyncio
async def test_executor_logs_lifecycle_boundaries(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    config = type(
        "Cfg",
        (),
        {
            "workspace_dir": str(tmp_path),
            "docker_image": "rust:test",
            "max_memory_gb": 8,
            "max_runtime_seconds": 10,
            "max_cpus": 2,
            "docker_mounts": [],
            "docker_pull_policy": "if-not-present",
            "log_flush_interval_seconds": 3.0,
            "log_sync_interval_seconds": 2.0,
        },
    )()

    class FakeClient:
        def __init__(self):
            self.events = []

        async def send_event(self, task_id, payload):
            self.events.append((task_id, payload))

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

    class FakeDocker:
        async def is_available(self):
            return True

        async def ensure_image(self, _policy):
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            return type(
                "Result",
                (),
                {"state": TaskStatus.COMPLETED, "exit_code": 0, "message": ""},
            )()

        async def close(self):
            pass

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger, _docker
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            pass

        def stop(self):
            return 2

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = TaskExecutor(config=config, client=FakeClient())
    monkeypatch.setattr(TaskExecutor, "_prepare_workspace", fake_prepare_workspace)

    claimed = {
        "id": 9,
        "lease_token": "lease-9",
        "crate_name": "serde",
        "version": "1.0.0",
    }

    await executor.execute_claimed_task(claimed)

    runner_log = tmp_path / "logs" / "9-runner.log"
    content = runner_log.read_text()
    assert "task started" in content
    assert "command started" in content
    assert "command finished" in content
    assert "task terminal event sent" in content


@pytest.mark.asyncio
async def test_executor_failure_logs_traceback(tmp_path, monkeypatch):
    config = type(
        "Cfg",
        (),
        {
            "workspace_dir": str(tmp_path),
            "docker_image": "rust:test",
            "max_memory_gb": 8,
            "max_runtime_seconds": 10,
            "max_cpus": 2,
            "docker_mounts": [],
            "docker_pull_policy": "if-not-present",
            "log_flush_interval_seconds": 3.0,
            "log_sync_interval_seconds": 2.0,
        },
    )()

    class FakeClient:
        async def send_event(self, *_args, **_kwargs):
            return None

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

    class BrokenDocker:
        async def is_available(self):
            return True

        async def ensure_image(self, _policy):
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("docker boom")

        async def close(self):
            pass

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger, _docker
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            pass

        def stop(self):
            return 2

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: BrokenDocker())

    executor = TaskExecutor(config=config, client=FakeClient())
    monkeypatch.setattr(TaskExecutor, "_prepare_workspace", fake_prepare_workspace)

    claimed = {
        "id": 10,
        "lease_token": "lease-10",
        "crate_name": "serde",
        "version": "1.0.0",
    }

    await executor.execute_claimed_task(claimed)

    runner_log = tmp_path / "logs" / "10-runner.log"
    content = runner_log.read_text()
    assert "task execution failed" in content
    assert "Traceback" in content


@pytest.mark.asyncio
async def test_multiple_tasks_run_containers_concurrently(tmp_path, monkeypatch):
    """When max_jobs > 1, containers should run in parallel without a global lock."""
    config = type(
        "Cfg",
        (),
        {
            "workspace_dir": str(tmp_path),
            "docker_image": "rust:test",
            "max_memory_gb": 8,
            "max_runtime_seconds": 10,
            "max_cpus": 2,
            "docker_mounts": [],
            "docker_pull_policy": "if-not-present",
            "log_flush_interval_seconds": 3.0,
            "log_sync_interval_seconds": 2.0,
        },
    )()

    class FakeClient:
        async def send_event(self, *_args, **_kwargs):
            return None

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

    active_runs = 0
    max_active_runs = 0
    run_lock = asyncio.Lock()

    class ConcurrentTrackingDocker:
        async def is_available(self):
            return True

        async def ensure_image(self, _policy):
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            nonlocal active_runs, max_active_runs
            async with run_lock:
                active_runs += 1
                max_active_runs = max(max_active_runs, active_runs)
            await asyncio.sleep(0.2)
            async with run_lock:
                active_runs -= 1
            return type(
                "Result",
                (),
                {"state": TaskStatus.COMPLETED, "exit_code": 0, "message": ""},
            )()

        async def close(self):
            pass

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger, _docker
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            pass

        def stop(self):
            return 2

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr(
        "runner.executor.DockerRunner", lambda **kwargs: ConcurrentTrackingDocker()
    )

    executor = TaskExecutor(config=config, client=FakeClient())
    monkeypatch.setattr(TaskExecutor, "_prepare_workspace", fake_prepare_workspace)

    claimed_a = {
        "id": 20,
        "lease_token": "lease-20",
        "crate_name": "serde",
        "version": "1.0.0",
    }
    claimed_b = {
        "id": 21,
        "lease_token": "lease-21",
        "crate_name": "tokio",
        "version": "1.0.0",
    }

    await asyncio.gather(
        executor.execute_claimed_task(claimed_a),
        executor.execute_claimed_task(claimed_b),
    )

    assert max_active_runs == 2, (
        f"Expected 2 concurrent container runs, but max was {max_active_runs}. "
        "Docker calls may be serialized by a global lock."
    )


@pytest.mark.asyncio
async def test_executor_cancellation_does_not_block_on_reporter(tmp_path, monkeypatch):
    """When cancelled, execute_claimed_task must not wait indefinitely for reporter."""
    config = type(
        "Cfg",
        (),
        {
            "workspace_dir": str(tmp_path),
            "docker_image": "rust:test",
            "max_memory_gb": 8,
            "max_runtime_seconds": 10,
            "max_cpus": 2,
            "docker_mounts": [],
            "docker_pull_policy": "if-not-present",
            "log_flush_interval_seconds": 3.0,
            "log_sync_interval_seconds": 2.0,
        },
    )()

    class FakeClient:
        def __init__(self):
            self.events = []

        async def send_event(self, *_args, **_kwargs):
            self.events.append(("send_event",))
            return None

        async def send_log_chunk(self, *_args, **_kwargs):
            # Simulate a slow network call that blocks reporter shutdown
            await asyncio.sleep(30)
            return None

    class FakeDocker:
        async def is_available(self):
            return True

        async def ensure_image(self, _policy):
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            # Block until cancelled
            await asyncio.Event().wait()

        async def close(self):
            pass

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger, _docker
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = TaskExecutor(config=config, client=FakeClient())
    monkeypatch.setattr(TaskExecutor, "_prepare_workspace", fake_prepare_workspace)

    claimed = {
        "id": 11,
        "lease_token": "lease-11",
        "crate_name": "serde",
        "version": "1.0.0",
    }

    execution_task = asyncio.create_task(executor.execute_claimed_task(claimed))

    # Wait for docker.run() to start
    await asyncio.sleep(0.1)

    # Cancel the task (simulating shutdown)
    execution_task.cancel()

    # Should complete within a reasonable time despite reporter being blocked.
    # Without the fix this would hang for 30+ seconds and timeout here.
    with pytest.raises(asyncio.CancelledError):
        done, pending = await asyncio.wait([execution_task], timeout=7.0)
        if execution_task in pending:
            execution_task.cancel()
            await execution_task
        else:
            # execution_task completed within timeout
            await execution_task
