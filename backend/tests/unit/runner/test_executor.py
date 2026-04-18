import pytest
import time
from types import SimpleNamespace

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
    """Regression test: _upload_logs must send stdout, stderr, runner, miri_report, stats-yaml with chunk_seq."""
    workspace = tmp_path / "workspace"
    logs_dir = workspace.parent / "logs"
    logs_dir.mkdir(parents=True)
    (workspace / "testgen").mkdir(parents=True)

    (logs_dir / "1-stdout.log").write_text("stdout content")
    (logs_dir / "1-stderr.log").write_text("stderr content")
    (logs_dir / "1-runner.log").write_text("runner content")
    (workspace / "testgen" / "miri_report.txt").write_text("miri content")
    (workspace / "testgen" / "stats.yaml").write_text("stats content")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append((task_id, log_type, payload))

    executor = object.__new__(TaskExecutor)
    executor.client = FakeClient()
    await executor._upload_logs(1, "lease-abc", workspace)

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
    """Regression test: blocking docker prechecks should not starve event loop heartbeats."""

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

    import asyncio

    ticker_task = asyncio.create_task(ticker())
    try:
        await executor.execute_claimed_task(claimed)
    finally:
        stop = True
        await ticker_task

    # If execute_claimed_task blocks the event loop, this tick count stays near zero.
    assert ticks >= 5
