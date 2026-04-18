import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from core.models import TaskStatus
from runner.executor import TaskExecutor


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
async def test_upload_logs_sends_all_log_types_with_chunk_seq(tmp_path):
    workspace_root = tmp_path / "workspace"
    task_workspace = workspace_root / "repos" / "test-crate-1.0.0"
    logs_dir = workspace_root / "logs"
    logs_dir.mkdir(parents=True)
    (task_workspace / "testgen").mkdir(parents=True)

    (logs_dir / "1-stdout.log").write_text("stdout content")
    (logs_dir / "1-stderr.log").write_text("stderr content")
    (logs_dir / "1-runner.log").write_text("runner content")
    (task_workspace / "testgen" / "miri_report.txt").write_text("miri content")
    (task_workspace / "testgen" / "stats.yaml").write_text("stats content")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append((task_id, log_type, payload))

    executor = object.__new__(TaskExecutor)
    executor.client = FakeClient()
    executor.config = type("Cfg", (), {"workspace_dir": str(workspace_root)})()
    await executor._upload_logs(1, "lease-abc", task_workspace)

    log_types = [c[1] for c in sent_chunks]
    assert "stdout" in log_types
    assert "stderr" in log_types
    assert "runner" in log_types
    assert "miri_report" in log_types
    assert "stats-yaml" in log_types

    for idx, (_, _, payload) in enumerate(sent_chunks, start=1):
        assert payload["chunk_seq"] == idx
        assert "lease_token" in payload
        assert "content" in payload


@pytest.mark.asyncio
async def test_execute_claimed_task_does_not_block_event_loop_during_docker_prechecks(
    tmp_path,
):
    class FakeClient:
        async def send_event(self, task_id, payload):
            return {"applied": True}

        async def send_log_chunk(self, task_id, log_type, payload):
            return {"appended": True}

    class FakeDocker:
        def is_available(self):
            time.sleep(0.4)
            return True

        def ensure_image(self, _pull_policy):
            time.sleep(0.4)
            return True

        async def run(self, command, workspace_dir, stdout_log, stderr_log):
            return SimpleNamespace(
                state=SimpleNamespace(value="completed"), exit_code=0, message=""
            )

    class FakeConfig:
        workspace_dir = str(tmp_path / "workspace")
        docker_pull_policy = "if-not-present"
        docker_image = "rust:test"

    executor = object.__new__(TaskExecutor)
    executor.config = FakeConfig()
    executor.client = FakeClient()
    executor.docker = FakeDocker()

    async def noop_prepare_workspace(workspace_dir, crate_name, version, task_logger):
        return None

    async def noop_upload_logs(task_id, lease_token, workspace_dir):
        return None

    executor._prepare_workspace = noop_prepare_workspace
    executor._upload_logs = noop_upload_logs
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
async def test_executor_upload_logs_include_decisions(tmp_path, caplog):
    workspace_root = tmp_path / "workspace"
    task_workspace = workspace_root / "repos" / "test-crate-1.0.0"
    logs_dir = workspace_root / "logs"
    logs_dir.mkdir(parents=True)
    (task_workspace / "testgen").mkdir(parents=True)

    (logs_dir / "7-stdout.log").write_text("stdout content")
    (logs_dir / "7-stderr.log").write_text("")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append((task_id, log_type, payload))

    executor = object.__new__(TaskExecutor)
    executor.client = FakeClient()
    executor.config = type("Cfg", (), {"workspace_dir": str(workspace_root)})()
    caplog.set_level(logging.INFO, logger="runner.executor")

    await executor._upload_logs(7, "lease-abc", task_workspace)

    assert sent_chunks
    assert any("log upload sent" in rec.message for rec in caplog.records)
    assert any("log upload empty" in rec.message for rec in caplog.records)
    assert any("log upload missing" in rec.message for rec in caplog.records)


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
        def is_available(self):
            return True

        def ensure_image(self, _policy):
            return True

        def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            return type(
                "Result",
                (),
                {"state": TaskStatus.COMPLETED, "exit_code": 0, "message": ""},
            )()

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    executor = TaskExecutor(config=config, client=FakeClient())
    executor.docker = FakeDocker()
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
        },
    )()

    class FakeClient:
        async def send_event(self, *_args, **_kwargs):
            return None

        async def send_log_chunk(self, *_args, **_kwargs):
            return None

    class BrokenDocker:
        def is_available(self):
            return True

        def ensure_image(self, _policy):
            return True

        def ensure_workspace_ownership(self, _workspace):
            return None

        async def run(self, *_args, **_kwargs):
            raise RuntimeError("docker boom")

    async def fake_prepare_workspace(
        self, workspace_dir, _crate_name, _version, _logger
    ):
        workspace_dir.mkdir(parents=True, exist_ok=True)

    executor = TaskExecutor(config=config, client=FakeClient())
    executor.docker = BrokenDocker()
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
