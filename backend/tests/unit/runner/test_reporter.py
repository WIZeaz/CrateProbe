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
async def test_reporter_progress_only_sends_when_counts_change(tmp_path):
    (tmp_path / "testgen" / "tests" / "a").mkdir(parents=True)
    (tmp_path / "testgen" / "poc" / "x").mkdir(parents=True)

    sent_events = []

    class FakeClient:
        async def send_event(self, task_id, payload):
            sent_events.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    reporter._last_progress_time = 0
    await reporter._maybe_send_progress()
    assert len(sent_events) == 1
    assert sent_events[0]["event_type"] == "progress"
    assert sent_events[0]["case_count"] == 1
    assert sent_events[0]["poc_count"] == 1

    reporter._last_progress_time = 0
    sent_events.clear()
    await reporter._maybe_send_progress()
    assert len(sent_events) == 0


@pytest.mark.asyncio
async def test_reporter_progress_respects_interval(tmp_path):
    (tmp_path / "testgen" / "tests" / "a").mkdir(parents=True)

    sent_events = []

    class FakeClient:
        async def send_event(self, task_id, payload):
            sent_events.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    # Set last_progress_time to now to simulate recent send
    reporter._last_progress_time = asyncio.get_running_loop().time()
    await reporter._maybe_send_progress()
    assert len(sent_events) == 0


@pytest.mark.asyncio
async def test_reporter_stop_returns_incrementing_seq(tmp_path):
    reporter = TaskReporter(
        client=type("C", (), {"send_log_chunk": lambda *a, **k: None})(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    seq1 = reporter.stop()
    seq2 = reporter.stop()
    assert seq1 == 2  # started uses 1
    assert seq2 == 3
    assert seq2 > seq1


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


def test_reporter_count_generated_items(tmp_path):
    testgen = tmp_path / "testgen"
    (testgen / "tests" / "a").mkdir(parents=True)
    (testgen / "tests" / "b").mkdir(parents=True)
    (testgen / "poc" / "x").mkdir(parents=True)
    (testgen / "poc" / "y").mkdir(parents=True)
    (testgen / "poc" / "z").mkdir(parents=True)

    reporter = object.__new__(TaskReporter)
    reporter.workspace_dir = tmp_path

    assert reporter._count_generated_items() == (2, 3)
