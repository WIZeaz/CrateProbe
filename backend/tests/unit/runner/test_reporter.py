import asyncio
import time

import pytest

from runner.reporter import TaskReporter


class RecordingClient:
    def __init__(self):
        self.calls = []

    async def push_file(self, job_id, payload):
        self.calls.append((job_id, dict(payload)))


def make_reporter(client, file_specs, flush_interval=3.0):
    return TaskReporter(
        client=client,
        job_id=7,
        lease_token="lease-7",
        file_specs=file_specs,
        flush_interval=flush_interval,
    )


@pytest.mark.asyncio
async def test_append_mode_sends_incremental_chunks_with_increasing_seq(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")
    client = RecordingClient()
    reporter = make_reporter(client, {"logs/stdout.log": (log_file, "append")})

    await reporter._flush_files()
    assert len(client.calls) == 1
    job_id, payload = client.calls[0]
    assert job_id == 7
    assert payload["path"] == "logs/stdout.log"
    assert payload["mode"] == "append"
    assert payload["chunk_seq"] == 1
    assert payload["content"] == "line 1\n"
    assert payload["lease_token"] == "lease-7"

    with open(log_file, "a") as f:
        f.write("line 2\n")

    await reporter._flush_files()
    assert len(client.calls) == 2
    _, payload = client.calls[1]
    assert payload["content"] == "line 2\n"
    assert payload["chunk_seq"] == 2


@pytest.mark.asyncio
async def test_append_mode_skips_unchanged_file(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    class ExplodingClient:
        def __init__(self):
            self.calls = 0

        async def push_file(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls > 1:
                raise AssertionError("should not push unchanged file")

    client = ExplodingClient()
    reporter = make_reporter(client, {"logs/stdout.log": (log_file, "append")})

    await reporter._flush_files()
    await reporter._flush_files()


@pytest.mark.asyncio
async def test_failed_push_retries_with_same_chunk_seq(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    class FailingOnceClient:
        def __init__(self):
            self.calls = []

        async def push_file(self, _job_id, payload):
            self.calls.append(dict(payload))
            if len(self.calls) == 1:
                raise RuntimeError("network error")

    client = FailingOnceClient()
    reporter = make_reporter(client, {"logs/stdout.log": (log_file, "append")})

    await reporter._flush_files()
    await reporter._flush_files()

    assert len(client.calls) == 2
    assert client.calls[0]["chunk_seq"] == 1
    # Offset and chunk_seq must not advance when the push fails.
    assert client.calls[1]["chunk_seq"] == 1
    assert client.calls[1]["content"] == "content"


@pytest.mark.asyncio
async def test_append_mode_handles_truncation(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("old content here")
    client = RecordingClient()
    reporter = make_reporter(client, {"logs/stdout.log": (log_file, "append")})

    await reporter._flush_files()
    assert client.calls[0][1]["content"] == "old content here"

    # Truncate file to smaller content: offset and chunk_seq reset.
    log_file.write_text("new")

    await reporter._flush_files()
    assert len(client.calls) == 2
    assert client.calls[1][1]["content"] == "new"
    assert client.calls[1][1]["chunk_seq"] == 1


@pytest.mark.asyncio
async def test_full_mode_resends_whole_file_each_flush(tmp_path):
    stats = tmp_path / "stats.yaml"
    stats.write_text("compile_failed: 329\n")
    client = RecordingClient()
    reporter = make_reporter(client, {"stats.yaml": (stats, "full")})

    await reporter._flush_files()
    await reporter._flush_files()
    assert len(client.calls) == 2
    for _, payload in client.calls:
        assert payload["path"] == "stats.yaml"
        assert payload["mode"] == "full"
        assert "chunk_seq" not in payload
        assert payload["content"] == "compile_failed: 329\n"

    # In-place rewrite with identical byte length must still be re-sent;
    # size-based dedup would leave the platform with a stale copy.
    stats.write_text("compile_failed: 336\n")
    assert stats.stat().st_size == len("compile_failed: 329\n")

    await reporter._flush_files()
    assert len(client.calls) == 3
    assert client.calls[2][1]["content"] == "compile_failed: 336\n"


@pytest.mark.asyncio
async def test_missing_file_is_skipped(tmp_path):
    missing = tmp_path / "nope.log"
    client = RecordingClient()
    reporter = make_reporter(client, {"logs/nope.log": (missing, "append")})

    await reporter._flush_files()
    assert client.calls == []

    missing.write_text("now exists\n")
    await reporter._flush_files()
    assert len(client.calls) == 1
    assert client.calls[0][1]["content"] == "now exists\n"


@pytest.mark.asyncio
async def test_flush_applies_each_files_own_mode(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("a\n")
    stats = tmp_path / "stats.yaml"
    stats.write_text("s1\n")
    client = RecordingClient()
    reporter = make_reporter(
        client,
        {
            "logs/stdout.log": (log_file, "append"),
            "stats.yaml": (stats, "full"),
        },
    )

    await reporter._flush_files()
    await reporter._flush_files()

    by_path = {}
    for _, payload in client.calls:
        by_path.setdefault(payload["path"], []).append(payload)

    # append file unchanged on the second flush: skipped.
    assert len(by_path["logs/stdout.log"]) == 1
    # full file: re-sent on every flush.
    assert len(by_path["stats.yaml"]) == 2


@pytest.mark.asyncio
async def test_push_exception_does_not_break_flush_loop(tmp_path):
    good = tmp_path / "good.log"
    good.write_text("good\n")
    bad = tmp_path / "bad.log"
    bad.write_text("bad\n")

    class PartiallyFailingClient:
        def __init__(self):
            self.calls = []

        async def push_file(self, _job_id, payload):
            if payload["path"] == "bad":
                raise RuntimeError("boom")
            self.calls.append(payload)

    client = PartiallyFailingClient()
    reporter = make_reporter(
        client,
        {"bad": (bad, "append"), "good": (good, "append")},
    )

    await reporter._flush_files()  # must not raise
    assert [c["path"] for c in client.calls] == ["good"]

    # The good file's offset advanced; a second flush sends nothing.
    await reporter._flush_files()
    assert len(client.calls) == 1


@pytest.mark.asyncio
async def test_run_loop_stops_and_flushes_remaining_content(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")
    client = RecordingClient()
    reporter = make_reporter(
        client, {"logs/stdout.log": (log_file, "append")}, flush_interval=0.05
    )

    run_task = asyncio.create_task(reporter.run())
    await asyncio.sleep(0.1)

    with open(log_file, "a") as f:
        f.write("line 2\n")

    reporter.stop()
    await asyncio.wait_for(run_task, timeout=5)

    contents = [payload["content"] for _, payload in client.calls]
    assert any("line 1" in c for c in contents)
    assert any("line 2" in c for c in contents)


@pytest.mark.asyncio
async def test_final_flush_after_stop_uses_shutdown_timeout(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("data\n")

    class HangingClient:
        async def push_file(self, *_args, **_kwargs):
            await asyncio.sleep(60)

    reporter = make_reporter(HangingClient(), {"logs/stdout.log": (log_file, "append")})
    reporter.stop()

    start = time.monotonic()
    await reporter.run()
    elapsed = time.monotonic() - start
    assert elapsed < 5


def test_reporter_stores_custom_flush_interval(tmp_path):
    reporter = make_reporter(RecordingClient(), {}, flush_interval=7.5)
    assert reporter.flush_interval == 7.5


def test_invalid_mode_raises_value_error(tmp_path):
    with pytest.raises(ValueError):
        make_reporter(
            RecordingClient(),
            {"logs/x.log": (tmp_path / "x.log", "chunk")},
        )
