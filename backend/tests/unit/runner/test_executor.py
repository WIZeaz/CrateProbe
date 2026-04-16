import pytest
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
