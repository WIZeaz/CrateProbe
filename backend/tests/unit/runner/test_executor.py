import asyncio
import io
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.models import TaskStatus
from runner.executor import TaskExecutor, _SyncState
from runner.task_cache import TaskFetchError

TASK_TOML = """
name = "demo-task"
image = "rust:test"
cmd = "cargo test"
workspace = "/work"
artifacts = ["out/result.txt", "reports"]
watch = ["stats.yaml"]

[limit]
cpu = 2
mem = "4g"
timeout = "1h"

[env]
RUST_LOG = "info"
"""


def make_config(tmp_path):
    return type(
        "Cfg",
        (),
        {
            "workspace_dir": str(tmp_path),
            "docker_pull_policy": "if-not-present",
            "log_flush_interval_seconds": 3.0,
            "log_sync_interval_seconds": 2.0,
            "state_sync_interval_seconds": 30.0,
        },
    )()


def make_claimed(job_id=1, task_id=7):
    return {
        "job_id": job_id,
        "lease_token": f"lease-{job_id}",
        "crate_name": "serde",
        "version": "1.0.0",
        "task_id": task_id,
        "task_sha256": "deadbeef",
    }


def write_crate(path: Path, crate_name: str, version: str, files=None) -> None:
    files = files or {"Cargo.toml": "[package]\n", "src/lib.rs": ""}
    with tarfile.open(path, "w:gz") as tar:
        for rel, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"{crate_name}-{version}/{rel}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def make_task_dir(root: Path, task_id: int = 7, toml_text: str = TASK_TOML) -> Path:
    task_dir = root / "task_cache" / str(task_id)
    (task_dir / "scripts").mkdir(parents=True)
    (task_dir / "task.toml").write_text(toml_text)
    # Cache marker written by TaskCache; must not leak into the job workspace.
    (task_dir / ".sha256").write_text("deadbeef\n")
    (task_dir / "scripts" / "run.sh").write_text("echo hi\n")
    (task_dir / "extra.txt").write_text("overlay\n")
    return task_dir


class FakeClient:
    def __init__(self):
        self.sync_payloads = []
        self.uploads = []

    async def sync_job(self, job_id, payload):
        self.sync_payloads.append((job_id, payload.copy()))
        return {"synced": True, "last_sync_seq": payload["sync_seq"]}

    async def upload_artifact(self, job_id, lease_token, rel_path, file_path):
        self.uploads.append((job_id, lease_token, rel_path))


class FakeDownloader:
    def __init__(self):
        self.downloads = []

    async def download(self, crate_name, version, output_path):
        self.downloads.append((crate_name, version))
        write_crate(output_path, crate_name, version)

    async def close(self):
        pass


class FakeTaskCache:
    def __init__(self, task_dir=None, error=None):
        self.task_dir = task_dir
        self.error = error
        self.calls = []

    async def ensure(self, task_id, sha256):
        self.calls.append((task_id, sha256))
        if self.error is not None:
            raise self.error
        return self.task_dir


class FakeDocker:
    """Spec-driven fake; ``run`` materializes artifacts and log files."""

    def __init__(self, tracker):
        self.tracker = tracker

    async def is_available(self):
        self.tracker["is_available"] += 1
        return True

    async def ensure_image(self, image):
        self.tracker["ensure_image"].append(image)
        return True

    async def ensure_workspace_ownership(
        self, workspace_dir, image, container_workspace
    ):
        self.tracker["chown"].append((workspace_dir, image, container_workspace))

    async def run(self, **kwargs):
        self.tracker["run_calls"].append(kwargs)
        workspace_dir = kwargs["workspace_dir"]
        (workspace_dir / "out").mkdir(parents=True, exist_ok=True)
        (workspace_dir / "out" / "result.txt").write_text("ok\n")
        (workspace_dir / "reports").mkdir(exist_ok=True)
        (workspace_dir / "reports" / "r1.txt").write_text("r1\n")
        (workspace_dir / "reports" / "r2.txt").write_text("r2\n")
        (workspace_dir / "stats.yaml").write_text("runs: 1\n")
        (workspace_dir / "stdout.log").write_text("hello stdout\n")
        (workspace_dir / "stderr.log").write_text("hello stderr\n")
        return SimpleNamespace(state=TaskStatus.COMPLETED, exit_code=0, message="")

    async def close(self):
        self.tracker["closed"] += 1


def new_tracker():
    return {
        "is_available": 0,
        "ensure_image": [],
        "chown": [],
        "run_calls": [],
        "closed": 0,
    }


class FakeReporter:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stop_event = asyncio.Event()
        FakeReporter.instances.append(self)

    async def run(self):
        await self.stop_event.wait()

    def stop(self):
        self.stop_event.set()


def install_fakes(monkeypatch, tracker):
    docker_factory_calls = []

    def factory(**kwargs):
        docker_factory_calls.append(kwargs)
        return FakeDocker(tracker)

    monkeypatch.setattr("runner.executor.DockerRunner", factory)
    FakeReporter.instances = []
    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
    return docker_factory_calls


def terminal_payloads(client):
    return [p for _job_id, p in client.sync_payloads if p["status"] != "running"]


@pytest.mark.asyncio
async def test_execute_claimed_job_happy_path(tmp_path, monkeypatch):
    task_dir = make_task_dir(tmp_path)
    client = FakeClient()
    tracker = new_tracker()
    docker_factory_calls = install_fakes(monkeypatch, tracker)
    cache = FakeTaskCache(task_dir=task_dir)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=cache,
    )

    await executor.execute_claimed_job(make_claimed(job_id=1, task_id=7))

    job_dir = tmp_path / "jobs" / "job1"

    # Task cache consulted with claimed identity.
    assert cache.calls == [(7, "deadbeef")]

    # DockerRunner built from runner-level config only.
    assert docker_factory_calls == [
        {
            "docker_pull_policy": "if-not-present",
            "log_sync_interval_seconds": 2.0,
        }
    ]

    # docker.run receives the spec values.
    assert tracker["is_available"] == 1
    assert tracker["ensure_image"] == ["rust:test"]
    assert len(tracker["run_calls"]) == 1
    run_kwargs = tracker["run_calls"][0]
    assert run_kwargs["image"] == "rust:test"
    assert run_kwargs["cmd"] == "cargo test"
    assert run_kwargs["workspace_dir"] == job_dir
    assert run_kwargs["container_workspace"] == "/work"
    assert run_kwargs["env"] == {"RUST_LOG": "info"}
    assert run_kwargs["cpu"] == 2.0
    assert run_kwargs["mem"] == "4g"
    assert run_kwargs["timeout_seconds"] == 3600
    assert run_kwargs["stdout_log"] == job_dir / "stdout.log"
    assert run_kwargs["stderr_log"] == job_dir / "stderr.log"

    # Crate extracted into the job dir, task zip overlaid, task.toml excluded.
    assert (job_dir / "Cargo.toml").exists()
    assert (job_dir / "src" / "lib.rs").exists()
    assert (job_dir / "extra.txt").read_text() == "overlay\n"
    assert (job_dir / "scripts" / "run.sh").exists()
    assert not (job_dir / "task.toml").exists()
    assert not (job_dir / ".sha256").exists()

    # Reporter pushes logs incrementally and watch files in full mode.
    assert len(FakeReporter.instances) == 1
    reporter = FakeReporter.instances[0]
    assert reporter.kwargs["job_id"] == 1
    assert reporter.kwargs["lease_token"] == "lease-1"
    runner_log = tmp_path / "logs" / "1-runner.log"
    assert reporter.kwargs["file_specs"] == {
        "stdout.log": (job_dir / "stdout.log", "append"),
        "stderr.log": (job_dir / "stderr.log", "append"),
        "runner.log": (runner_log, "append"),
        "stats.yaml": (job_dir / "stats.yaml", "full"),
    }

    # Artifacts uploaded one request per file, paths relative to the workspace.
    assert sorted(u[2] for u in client.uploads) == [
        "out/result.txt",
        "reports/r1.txt",
        "reports/r2.txt",
    ]
    assert all(u[0] == 1 and u[1] == "lease-1" for u in client.uploads)

    # Sync payloads never carry the retired stats fields.
    assert client.sync_payloads
    for payload_job_id, payload in client.sync_payloads:
        assert payload_job_id == 1
        assert "case_count" not in payload
        assert "poc_count" not in payload
        assert "compile_failed" not in payload

    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "completed"
    assert terminal[0]["exit_code"] == 0
    assert terminal[0]["lease_token"] == "lease-1"
    assert tracker["closed"] == 1


@pytest.mark.asyncio
async def test_task_fetch_error_marks_job_failed_without_starting_docker(
    tmp_path, monkeypatch
):
    client = FakeClient()
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)
    cache = FakeTaskCache(
        error=TaskFetchError("task 7 zip not found on backend (HTTP 404)")
    )

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=cache,
    )

    await executor.execute_claimed_job(make_claimed(job_id=1, task_id=7))

    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "failed"
    assert "task 7 zip not found on backend" in terminal[0]["message"]

    # Docker never touched and the job workspace never created.
    assert tracker["is_available"] == 0
    assert tracker["ensure_image"] == []
    assert tracker["run_calls"] == []
    assert not (tmp_path / "jobs" / "job1").exists()
    assert FakeReporter.instances == []

    # Terminal ack still completed via the state sync loop.
    content = (tmp_path / "logs" / "1-runner.log").read_text()
    assert "task fetch failed" in content
    assert "terminal sync acked" in content


@pytest.mark.asyncio
async def test_missing_task_toml_fails_via_task_fetch_log_path(tmp_path, monkeypatch):
    """A cached task dir without task.toml fails the job through the same
    'task fetch failed' path as download errors (terminal semantics
    unchanged)."""
    task_dir = tmp_path / "task_cache" / "7"
    task_dir.mkdir(parents=True)
    (task_dir / ".sha256").write_text("deadbeef\n")
    client = FakeClient()
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed(job_id=1, task_id=7))

    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "failed"
    assert "task.toml" in terminal[0]["message"]

    # Docker never touched.
    assert tracker["is_available"] == 0
    assert tracker["run_calls"] == []

    content = (tmp_path / "logs" / "1-runner.log").read_text()
    assert "task fetch failed" in content


@pytest.mark.asyncio
async def test_missing_artifact_path_warns_and_skips(tmp_path, monkeypatch):
    toml_text = TASK_TOML.replace(
        'artifacts = ["out/result.txt", "reports"]',
        'artifacts = ["missing.txt", "out/result.txt"]',
    )
    task_dir = make_task_dir(tmp_path, toml_text=toml_text)
    client = FakeClient()
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed())

    assert [u[2] for u in client.uploads] == ["out/result.txt"]
    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "completed"

    content = (tmp_path / "logs" / "1-runner.log").read_text()
    assert "artifact path missing" in content
    assert "missing.txt" in content


@pytest.mark.asyncio
async def test_artifact_upload_failure_does_not_fail_job(tmp_path, monkeypatch):
    class FlakyUploadClient(FakeClient):
        async def upload_artifact(self, job_id, lease_token, rel_path, file_path):
            await super().upload_artifact(job_id, lease_token, rel_path, file_path)
            raise RuntimeError("upload boom")

    task_dir = make_task_dir(tmp_path)
    client = FlakyUploadClient()
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed())

    # All artifacts were attempted despite individual failures.
    assert sorted(u[2] for u in client.uploads) == [
        "out/result.txt",
        "reports/r1.txt",
        "reports/r2.txt",
    ]
    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "completed"

    content = (tmp_path / "logs" / "1-runner.log").read_text()
    assert "artifact upload failed" in content
    assert "upload boom" in content


@pytest.mark.asyncio
async def test_existing_job_dir_is_wiped_and_recreated(tmp_path, monkeypatch):
    task_dir = make_task_dir(tmp_path)
    job_dir = tmp_path / "jobs" / "job1"
    job_dir.mkdir(parents=True)
    (job_dir / "stale.txt").write_text("old")
    client = FakeClient()
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed())

    assert not (job_dir / "stale.txt").exists()
    assert (job_dir / "Cargo.toml").exists()
    assert (job_dir / "extra.txt").exists()
    # Ownership normalized via the spec image/workspace before the wipe.
    assert tracker["chown"] == [(job_dir, "rust:test", "/work")]


@pytest.mark.asyncio
async def test_execute_claimed_job_does_not_block_event_loop_during_docker_prechecks(
    tmp_path, monkeypatch
):
    task_dir = make_task_dir(tmp_path)

    class SlowDocker(FakeDocker):
        async def is_available(self):
            await asyncio.sleep(0.4)
            return True

        async def ensure_image(self, image):
            await asyncio.sleep(0.4)
            return True

    tracker = new_tracker()
    monkeypatch.setattr(
        "runner.executor.DockerRunner", lambda **kwargs: SlowDocker(tracker)
    )
    FakeReporter.instances = []
    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks, stop
        while not stop:
            await asyncio.sleep(0.05)
            ticks += 1

    ticker_task = asyncio.create_task(ticker())
    try:
        await executor.execute_claimed_job(make_claimed())
    finally:
        stop = True
        await ticker_task

    assert ticks >= 5


@pytest.mark.asyncio
async def test_executor_logs_lifecycle_boundaries(tmp_path, monkeypatch):
    task_dir = make_task_dir(tmp_path)
    tracker = new_tracker()
    install_fakes(monkeypatch, tracker)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed(job_id=9))

    runner_log = tmp_path / "logs" / "9-runner.log"
    content = runner_log.read_text()
    assert "job started" in content
    assert "command started" in content
    assert "command finished" in content
    assert "terminal sync acked" in content


@pytest.mark.asyncio
async def test_executor_failure_logs_traceback(tmp_path, monkeypatch):
    task_dir = make_task_dir(tmp_path)
    client = FakeClient()

    class BrokenDocker(FakeDocker):
        async def run(self, **kwargs):
            raise RuntimeError("docker boom")

    tracker = new_tracker()
    monkeypatch.setattr(
        "runner.executor.DockerRunner", lambda **kwargs: BrokenDocker(tracker)
    )
    FakeReporter.instances = []
    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await executor.execute_claimed_job(make_claimed(job_id=10))

    runner_log = tmp_path / "logs" / "10-runner.log"
    content = runner_log.read_text()
    assert "job execution failed" in content
    assert "Traceback" in content

    terminal = terminal_payloads(client)
    assert len(terminal) == 1
    assert terminal[0]["status"] == "failed"
    assert "docker boom" in terminal[0]["message"]


@pytest.mark.asyncio
async def test_multiple_jobs_run_containers_concurrently(tmp_path, monkeypatch):
    """When max_jobs > 1, containers should run in parallel without a global lock."""
    task_dir = make_task_dir(tmp_path)
    client = FakeClient()

    active_runs = 0
    max_active_runs = 0
    run_lock = asyncio.Lock()

    class ConcurrentTrackingDocker(FakeDocker):
        async def run(self, **kwargs):
            nonlocal active_runs, max_active_runs
            async with run_lock:
                active_runs += 1
                max_active_runs = max(max_active_runs, active_runs)
            await asyncio.sleep(0.2)
            async with run_lock:
                active_runs -= 1
            return SimpleNamespace(state=TaskStatus.COMPLETED, exit_code=0, message="")

    tracker = new_tracker()
    monkeypatch.setattr(
        "runner.executor.DockerRunner",
        lambda **kwargs: ConcurrentTrackingDocker(tracker),
    )
    FakeReporter.instances = []
    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=client,
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    await asyncio.gather(
        executor.execute_claimed_job(make_claimed(job_id=20)),
        executor.execute_claimed_job(make_claimed(job_id=21)),
    )

    assert max_active_runs == 2, (
        f"Expected 2 concurrent container runs, but max was {max_active_runs}. "
        "Docker calls may be serialized by a global lock."
    )


@pytest.mark.asyncio
async def test_executor_cancellation_does_not_block_on_reporter(tmp_path, monkeypatch):
    """When cancelled, execute_claimed_job must not wait indefinitely for reporter."""
    task_dir = make_task_dir(tmp_path)
    monkeypatch.setattr("runner.executor._REPORTER_SHUTDOWN_TIMEOUT_SECONDS", 0.5)

    class StuckReporter:
        def __init__(self, **kwargs):
            pass

        async def run(self):
            # Simulate a reporter stuck in a slow network call that ignores stop().
            await asyncio.Event().wait()

        def stop(self):
            pass

    class BlockingDocker(FakeDocker):
        async def run(self, **kwargs):
            await asyncio.Event().wait()

    tracker = new_tracker()
    monkeypatch.setattr(
        "runner.executor.DockerRunner", lambda **kwargs: BlockingDocker(tracker)
    )
    monkeypatch.setattr("runner.executor.TaskReporter", StuckReporter)

    executor = TaskExecutor(
        config=make_config(tmp_path),
        client=FakeClient(),
        crate_downloader=FakeDownloader(),
        task_cache=FakeTaskCache(task_dir=task_dir),
    )

    execution_task = asyncio.create_task(
        executor.execute_claimed_job(make_claimed(job_id=11))
    )

    # Wait for docker.run() to start.
    await asyncio.sleep(0.1)

    execution_task.cancel()

    # Should complete within a reasonable time despite the stuck reporter.
    done, pending = await asyncio.wait([execution_task], timeout=7.0)
    assert execution_task in done, "executor shutdown blocked on the reporter"
    with pytest.raises(asyncio.CancelledError):
        await execution_task


@pytest.mark.asyncio
async def test_running_sync_in_flight_does_not_ack_terminal(tmp_path):
    """Regression: a running payload in flight must not ack terminal."""
    executor = object.__new__(TaskExecutor)

    running_started = asyncio.Event()
    running_released = asyncio.Event()
    terminal_observed = asyncio.Event()

    class DelayedAckClient:
        def __init__(self):
            self.payloads = []

        async def sync_job(self, job_id, payload):
            self.payloads.append(payload.copy())
            if payload["status"] == "running" and not running_released.is_set():
                running_started.set()
                await running_released.wait()
            elif payload["status"] != "running":
                terminal_observed.set()
            return {"synced": True, "last_sync_seq": payload["sync_seq"]}

    executor.client = DelayedAckClient()
    sync_state = _SyncState()

    loop_task = asyncio.create_task(
        executor._state_sync_loop(
            job_id=1,
            lease_token="lease-1",
            interval=30.0,
            sync_state=sync_state,
        )
    )

    try:
        # Wait until the running payload is blocked inside client.sync_job.
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
