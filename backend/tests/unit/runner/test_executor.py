import asyncio
import contextlib
import logging
import shutil
import time
from types import SimpleNamespace

import pytest

from core.models import TaskStatus
from runner.executor import TaskExecutor, _SyncState
from runner.reporter import TaskReporter


class FakeDownloader:
    async def download(self, *args, **kwargs):
        pass

    async def close(self):
        pass

    async def resolve_version(self, crate_name, version=None):
        return version or "1.0.0"


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
        async def sync_task(self, task_id, payload):
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

        async def send_log_chunk(self, task_id, log_type, payload):
            return {"appended": True}

        async def send_log(self, task_id, log_type, payload):
            return {"written": True}

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
        state_sync_interval_seconds = 30.0
        max_memory_gb = 8
        max_runtime_seconds = 10
        max_cpus = 2
        docker_mounts = []

    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = object.__new__(TaskExecutor)
    executor.config = FakeConfig()
    executor.client = FakeClient()
    executor.crate_downloader = FakeDownloader()

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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    class FakeClient:
        def __init__(self):
            self.syncs = []

        async def sync_task(self, task_id, payload):
            self.syncs.append((task_id, payload))
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

        async def send_log(self, *_args, **_kwargs):
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
            pass

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = TaskExecutor(
        config=config,
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
    )
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
    assert "terminal sync acked" in content


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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    class FakeClient:
        async def sync_task(self, *_args, **kwargs):
            payload = kwargs.get("payload") or (_args[1] if len(_args) > 1 else {})
            return {"synced": True, "last_sync_seq": payload.get("sync_seq", 1)}

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

        async def send_log(self, *_args, **_kwargs):
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
            pass

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: BrokenDocker())

    executor = TaskExecutor(
        config=config,
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
    )
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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    class FakeClient:
        async def sync_task(self, *_args, **kwargs):
            payload = kwargs.get("payload") or (_args[1] if len(_args) > 1 else {})
            return {"synced": True, "last_sync_seq": payload.get("sync_seq", 1)}

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

        async def send_log(self, *_args, **_kwargs):
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
            pass

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr(
        "runner.executor.DockerRunner", lambda **kwargs: ConcurrentTrackingDocker()
    )

    executor = TaskExecutor(
        config=config,
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
    )
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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    class FakeClient:
        def __init__(self):
            self.syncs = []

        async def sync_task(self, task_id, payload):
            self.syncs.append(("sync_task", task_id, payload))
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

        async def send_log_chunk(self, *_args, **_kwargs):
            # Simulate a slow network call that blocks reporter shutdown
            await asyncio.sleep(30)
            return None

        async def send_log(self, *_args, **_kwargs):
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

    executor = TaskExecutor(
        config=config,
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
    )
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


@pytest.mark.asyncio
async def test_state_sync_loop_starts_after_prepare_workspace(tmp_path, monkeypatch):
    """Regression: sync loop must not read stats.yaml before workspace is wiped."""
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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    workspace_dir = tmp_path / "repos" / "serde-1.0.0"
    stale_stats = workspace_dir / "testgen" / "stats.yaml"
    stale_stats.parent.mkdir(parents=True)
    stale_stats.write_text("CompileFailed: 999\n")

    class FakeClient:
        def __init__(self):
            self.sync_payloads = []

        async def sync_task(self, task_id, payload):
            self.sync_payloads.append(payload.copy())
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

        async def send_log(self, *_args, **_kwargs):
            return None

    class FakeDocker:
        async def is_available(self):
            return True

        async def ensure_image(self, _policy):
            return True

        async def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            # Block so the task stays running and the sync loop can iterate.
            await asyncio.Event().wait()

        async def close(self):
            pass

    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    monkeypatch.setattr("runner.executor.DockerRunner", lambda **kwargs: FakeDocker())

    executor = TaskExecutor(
        config=config,
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
    )

    prepare_started = asyncio.Event()
    prepare_done = asyncio.Event()
    reads = []

    async def instrumented_prepare_workspace(
        self, workspace_dir, crate_name, version, task_logger, docker
    ):
        prepare_started.set()
        # Yield to give the sync loop a chance to run before deletion.
        await asyncio.sleep(0)
        if workspace_dir.exists():
            await asyncio.to_thread(shutil.rmtree, workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        prepare_done.set()

    def instrumented_get_compile_failed_count(workspace_dir):
        stats_path = workspace_dir / "testgen" / "stats.yaml"
        reads.append(
            {
                "workspace_existed": workspace_dir.exists(),
                "stats_existed": stats_path.exists(),
                "stats_value": stats_path.read_text() if stats_path.exists() else None,
            }
        )
        return None

    monkeypatch.setattr(
        TaskExecutor, "_prepare_workspace", instrumented_prepare_workspace
    )
    monkeypatch.setattr(
        TaskExecutor, "_get_compile_failed_count", instrumented_get_compile_failed_count
    )
    monkeypatch.setattr(
        TaskExecutor, "_count_generated_items", lambda _workspace_dir: (0, 0)
    )

    claimed = {
        "id": 50,
        "lease_token": "lease-50",
        "crate_name": "serde",
        "version": "1.0.0",
    }

    execution_task = asyncio.create_task(executor.execute_claimed_task(claimed))
    try:
        await asyncio.wait_for(prepare_done.wait(), timeout=2.0)
        # Allow a few event loop iterations for any early sync loop runs.
        await asyncio.sleep(0.1)
    finally:
        execution_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await execution_task

    # No sync payload should carry the stale 999 value.
    stale_payloads = [
        p for p in executor.client.sync_payloads if p.get("compile_failed") == 999
    ]
    assert (
        not stale_payloads
    ), "sync loop observed stale compile_failed before workspace wipe"

    # No read should have observed the stale stats.yaml file.
    stale_reads = [r for r in reads if r["stats_existed"]]
    assert (
        not stale_reads
    ), "_get_compile_failed_count read stats.yaml before workspace deletion"


@pytest.mark.asyncio
async def test_running_sync_in_flight_does_not_ack_terminal(tmp_path, monkeypatch):
    """Regression: a running payload in flight must not ack terminal."""
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
            "state_sync_interval_seconds": 30.0,
        },
    )()

    executor = object.__new__(TaskExecutor)
    executor.config = config

    running_started = asyncio.Event()
    running_released = asyncio.Event()
    terminal_observed = asyncio.Event()

    class DelayedAckClient:
        def __init__(self):
            self.payloads = []

        async def sync_task(self, task_id, payload):
            self.payloads.append(payload.copy())
            if payload["status"] == "running" and not running_released.is_set():
                running_started.set()
                await running_released.wait()
            elif payload["status"] != "running":
                terminal_observed.set()
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

    executor.client = DelayedAckClient()
    sync_state = _SyncState()

    monkeypatch.setattr(
        executor, "_count_generated_items", lambda _workspace_dir: (0, 0)
    )
    monkeypatch.setattr(
        executor, "_get_compile_failed_count", lambda _workspace_dir: None
    )

    loop_task = asyncio.create_task(
        executor._state_sync_loop(
            task_id=1,
            lease_token="lease-1",
            workspace_dir=tmp_path / "workspace",
            interval=30.0,
            sync_state=sync_state,
        )
    )

    try:
        # Wait until the running payload is blocked inside client.sync_task.
        await asyncio.wait_for(running_started.wait(), timeout=2.0)
        # Flip to terminal while the running payload is still in flight.
        sync_state.set_terminal(status="completed", exit_code=0, message="done")
        # Give the buggy ack logic a chance to fire if present.
        await asyncio.sleep(0.1)
        assert (
            not sync_state.terminal_acked.is_set()
        ), "running payload incorrectly acked terminal"

        # Release the running response and wait for the real terminal sync.
        running_released.set()
        await asyncio.wait_for(terminal_observed.wait(), timeout=2.0)
        await asyncio.sleep(0.1)
        assert sync_state.terminal_acked.is_set(), "terminal payload was not acked"
    finally:
        sync_state.terminal_acked.set()
        await loop_task
