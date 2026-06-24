import asyncio
import pytest
from pathlib import Path
from runner.reporter import TaskReporter


@pytest.mark.asyncio
async def test_reporter_flush_logs_sends_incremental_chunks(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append((task_id, log_type, payload))

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
        upload_config={"stdout": "chunk"},
    )

    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0][1] == "stdout"
    assert sent_chunks[0][2]["chunk_seq"] == 1
    assert sent_chunks[0][2]["content"] == "line 1\n"
    assert sent_chunks[0][2]["lease_token"] == "lease-1"

    with open(log_file, "a") as f:
        f.write("line 2\n")

    sent_chunks.clear()
    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0][2]["content"] == "line 2\n"
    assert sent_chunks[0][2]["chunk_seq"] == 2


@pytest.mark.asyncio
async def test_reporter_flush_logs_skips_unchanged_file(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    class FakeClient:
        async def send_log_chunk(self, *_args, **_kwargs):
            raise AssertionError("should not be called")

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
        upload_config={"stdout": "chunk"},
    )

    await reporter._flush_logs()
    await reporter._flush_logs()


@pytest.mark.asyncio
async def test_reporter_flush_logs_retries_on_failure(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    call_count = 0

    class FailingClient:
        async def send_log_chunk(self, *_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("network error")

    reporter = TaskReporter(
        client=FailingClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
        upload_config={"stdout": "chunk"},
    )

    await reporter._flush_logs()
    assert call_count == 1

    await reporter._flush_logs()
    assert call_count == 2


@pytest.mark.asyncio
async def test_reporter_flush_logs_handles_truncation(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("old content here")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
        upload_config={"stdout": "chunk"},
    )

    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0]["content"] == "old content here"

    # Truncate file to smaller content
    log_file.write_text("new")

    sent_chunks.clear()
    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0]["content"] == "new"
    assert sent_chunks[0]["chunk_seq"] == 1


@pytest.mark.asyncio
async def test_reporter_run_loop_stops_on_event(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
        upload_config={"stdout": "chunk"},
    )

    run_task = asyncio.create_task(reporter.run())
    await asyncio.sleep(0.1)

    # Write more content while reporter is running
    with open(log_file, "a") as f:
        f.write("line 2\n")

    reporter.stop()
    await run_task

    # Should have received both chunks (initial + after stop final flush)
    contents = [c["content"] for c in sent_chunks]
    assert any("line 1" in c for c in contents)
    assert any("line 2" in c for c in contents)


def test_reporter_uses_custom_flush_interval(tmp_path):
    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            pass

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
        log_flush_interval=7.5,
    )

    assert reporter.log_flush_interval == 7.5


@pytest.mark.asyncio
async def test_reporter_flush_logs_defaults_to_full_upload(tmp_path):
    log_file = tmp_path / "stats.yaml"
    log_file.write_text("CompileFailed: 1\n")

    sent_logs = []

    class FakeClient:
        async def send_log(self, task_id, log_type, payload):
            sent_logs.append((task_id, log_type, payload))

        async def send_log_chunk(self, *_args, **_kwargs):
            raise AssertionError("chunk upload should not be called")

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stats-yaml": log_file},
        workspace_dir=tmp_path,
        upload_config={},
    )

    await reporter._flush_logs()
    assert len(sent_logs) == 1
    assert sent_logs[0][1] == "stats-yaml"
    assert sent_logs[0][2]["content"] == "CompileFailed: 1\n"
    assert sent_logs[0][2]["lease_token"] == "lease-1"


@pytest.mark.asyncio
async def test_reporter_full_mode_reuploads_on_same_size_content_change(tmp_path):
    # stats.yaml is rewritten in place; when a count changes without changing
    # the file's byte length (e.g. 329 -> 336), size-based dedup would wrongly
    # skip the upload and leave the platform with a stale copy.
    log_file = tmp_path / "stats.yaml"
    log_file.write_text("compile_failed: 329\n")

    sent_logs = []

    class FakeClient:
        async def send_log(self, task_id, log_type, payload):
            sent_logs.append(payload)

        async def send_log_chunk(self, *_args, **_kwargs):
            raise AssertionError("chunk upload should not be called")

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stats-yaml": log_file},
        workspace_dir=tmp_path,
        upload_config={"stats-yaml": "full"},
    )

    await reporter._flush_logs()
    assert len(sent_logs) == 1
    assert sent_logs[0]["content"] == "compile_failed: 329\n"

    # In-place rewrite, identical byte length, different content.
    log_file.write_text("compile_failed: 336\n")
    assert log_file.stat().st_size == len("compile_failed: 329\n")

    sent_logs.clear()
    await reporter._flush_logs()
    assert len(sent_logs) == 1
    assert sent_logs[0]["content"] == "compile_failed: 336\n"


@pytest.mark.asyncio
async def test_reporter_invalid_upload_config_falls_back_to_full(tmp_path):
    log_file = tmp_path / "stats.yaml"
    log_file.write_text("CompileFailed: 2\n")

    sent_logs = []

    class FakeClient:
        async def send_log(self, task_id, log_type, payload):
            sent_logs.append((task_id, log_type, payload))

        async def send_log_chunk(self, *_args, **_kwargs):
            raise AssertionError("chunk upload should not be called")

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stats-yaml": log_file},
        workspace_dir=tmp_path,
        upload_config={"stats-yaml": "invalid-mode"},
    )

    await reporter._flush_logs()
    assert len(sent_logs) == 1
    assert sent_logs[0][2]["content"] == "CompileFailed: 2\n"
