import asyncio
import contextlib
import logging
import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Optional

from core.task_spec import parse_task_spec
from runner.client import RunnerControlClient
from runner.config import RunnerConfig
from runner.crate_downloader import CrateDownloader
from runner.crates_api import CratesAPI
from runner.docker_runner import DockerRunner
from runner.reporter import TaskReporter
from runner.task_cache import TaskCache, TaskFetchError

logger = logging.getLogger(__name__)

_REPORTER_SHUTDOWN_TIMEOUT_SECONDS = 30.0
_TERMINAL_ACK_TIMEOUT_SECONDS = 300.0
_SYNC_LOOP_SHUTDOWN_TIMEOUT_SECONDS = 10.0


class _SyncState:
    def __init__(self):
        self.status = "running"
        self.exit_code = None
        self.message = None
        self.wake_event = asyncio.Event()
        self.terminal_acked = asyncio.Event()
        self._next_seq = 1

    def set_terminal(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.wake_event.set()

    def next_seq(self) -> int:
        seq = self._next_seq
        self._next_seq += 1
        return seq


class TaskExecutor:
    def __init__(
        self,
        config: RunnerConfig,
        client: RunnerControlClient,
        crate_downloader: Optional[CrateDownloader] = None,
        task_cache: Optional[TaskCache] = None,
    ):
        self.config = config
        self.client = client
        self.crate_downloader = crate_downloader or CrateDownloader(
            crates_api=CratesAPI(
                user_agent=config.crates_io_user_agent,
            ),
            max_concurrent_downloads=config.crates_io_max_concurrent_downloads,
        )
        self.task_cache = task_cache or TaskCache(
            Path(config.workspace_dir) / "task_cache", client
        )

    async def close(self):
        await self.crate_downloader.close()

    async def execute_claimed_job(self, claimed: dict) -> None:
        job_id = claimed["job_id"]
        lease_token = claimed["lease_token"]
        crate_name = claimed["crate_name"]
        crate_version = claimed["version"]
        task_id = claimed["task_id"]
        task_sha256 = claimed["task_sha256"]

        # Each job gets its own DockerRunner so concurrent containers never
        # share runner state; per-job settings (image, limits, command) are
        # supplied per run from the task spec.
        docker = DockerRunner(
            docker_pull_policy=self.config.docker_pull_policy,
            mounts=self.config.docker_mounts,
            log_sync_interval_seconds=self.config.log_sync_interval_seconds,
        )

        workspace_root = Path(self.config.workspace_dir)
        job_dir = workspace_root / "jobs" / f"job{job_id}"
        logs_dir = workspace_root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runner_log = logs_dir / f"{job_id}-runner.log"
        stdout_log = job_dir / "stdout.log"
        stderr_log = job_dir / "stderr.log"

        handler = logging.FileHandler(str(runner_log), mode="w")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        job_logger = logging.getLogger(f"job.{job_id}")
        job_logger.setLevel(logging.DEBUG)
        job_logger.handlers.clear()
        job_logger.addHandler(handler)

        job_ctx = {
            "job_id": job_id,
            "task_id": task_id,
            "crate_name": crate_name,
            "version": crate_version,
        }

        sync_state = _SyncState()
        # The sync loop runs for the whole job so terminal states are acked
        # even when the job fails before reaching docker (e.g. task zip
        # fetch errors).
        sync_task = asyncio.create_task(
            self._state_sync_loop(
                job_id,
                lease_token,
                self.config.state_sync_interval_seconds,
                sync_state,
            )
        )

        reporter = None
        reporter_task = None

        terminal_status = None
        terminal_exit_code = None
        terminal_message = None

        try:
            job_logger.info("job started", extra=job_ctx)

            try:
                task_dir = await self.task_cache.ensure(task_id, task_sha256)
                # A missing task.toml is a fetch-level failure too: route it
                # through the same "task fetch failed" log path (terminal
                # semantics unchanged).
                spec = self._load_task_spec(task_dir, task_id)
            except TaskFetchError as exc:
                job_logger.error("task fetch failed: %s", exc, extra=job_ctx)
                terminal_status = "failed"
                terminal_message = str(exc)
            else:
                job_logger.info(
                    "task spec loaded",
                    extra={**job_ctx, "task_name": spec.name, "image": spec.image},
                )

                if not await docker.is_available():
                    raise RuntimeError("Docker is not available")
                if not await docker.ensure_image(spec.image):
                    raise RuntimeError(f"Docker image {spec.image} is not available")

                await self._prepare_workspace(
                    job_dir, crate_name, crate_version, job_logger, docker, spec
                )
                await asyncio.to_thread(self._overlay_task_files, task_dir, job_dir)
                job_logger.info("workspace prepared", extra=job_ctx)

                file_specs = {
                    "stdout.log": (stdout_log, "append"),
                    "stderr.log": (stderr_log, "append"),
                    "runner.log": (runner_log, "append"),
                }
                for watch_path in spec.watch:
                    file_specs[watch_path] = (job_dir / watch_path, "full")
                reporter = TaskReporter(
                    client=self.client,
                    job_id=job_id,
                    lease_token=lease_token,
                    file_specs=file_specs,
                    flush_interval=self.config.log_flush_interval_seconds,
                )
                reporter_task = asyncio.create_task(reporter.run())

                job_logger.info(
                    "command started",
                    extra={**job_ctx, "command_summary": spec.cmd},
                )
                command_started_at = time.monotonic()
                result = await docker.run(
                    image=spec.image,
                    cmd=spec.cmd,
                    workspace_dir=job_dir,
                    container_workspace=spec.workspace,
                    env=spec.env,
                    cpu=spec.limits.cpu,
                    mem=spec.limits.mem,
                    timeout_seconds=spec.limits.timeout_seconds,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                )
                command_duration_ms = int(
                    (time.monotonic() - command_started_at) * 1000
                )
                job_logger.info(
                    "command finished",
                    extra={
                        **job_ctx,
                        "exit_code": result.exit_code,
                        "duration_ms": command_duration_ms,
                    },
                )

                await self._upload_artifacts(
                    job_id, lease_token, job_dir, spec.artifacts, job_logger, job_ctx
                )

                terminal_status = result.state.value
                terminal_exit_code = result.exit_code
                terminal_message = result.message
        except asyncio.CancelledError:
            job_logger.info("job cancelled", extra=job_ctx)
            terminal_status = "failed"
            terminal_message = "Job interrupted by shutdown"
            raise
        except Exception as exc:
            job_logger.exception("job execution failed", extra=job_ctx)
            terminal_status = "failed"
            terminal_message = str(exc)
        finally:
            await self._shutdown_job(
                reporter=reporter,
                reporter_task=reporter_task,
                sync_state=sync_state,
                sync_task=sync_task,
                terminal_status=terminal_status,
                terminal_exit_code=terminal_exit_code,
                terminal_message=terminal_message,
                job_logger=job_logger,
                handler=handler,
                docker=docker,
                job_ctx=job_ctx,
            )

    async def _shutdown_job(
        self,
        reporter,
        reporter_task,
        sync_state,
        sync_task,
        terminal_status,
        terminal_exit_code,
        terminal_message,
        job_logger,
        handler,
        docker,
        job_ctx,
    ):
        if reporter is not None:
            reporter.stop()
            try:
                await asyncio.wait_for(
                    reporter_task, timeout=_REPORTER_SHUTDOWN_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                reporter_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await reporter_task

        if terminal_status is not None:
            sync_state.set_terminal(
                status=terminal_status,
                exit_code=terminal_exit_code,
                message=terminal_message,
            )
            try:
                await asyncio.wait_for(
                    sync_state.terminal_acked.wait(),
                    timeout=_TERMINAL_ACK_TIMEOUT_SECONDS,
                )
                job_logger.info("terminal sync acked", extra=job_ctx)
            except asyncio.TimeoutError:
                job_logger.warning(
                    "terminal sync not acked within timeout", extra=job_ctx
                )

        sync_state.terminal_acked.set()
        try:
            await asyncio.wait_for(
                sync_task, timeout=_SYNC_LOOP_SHUTDOWN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sync_task

        job_logger.info("job runner log closed", extra=job_ctx)
        job_logger.removeHandler(handler)
        handler.close()
        await docker.close()

    async def _state_sync_loop(
        self,
        job_id: int,
        lease_token: str,
        interval: float,
        sync_state: _SyncState,
    ) -> None:
        while not sync_state.terminal_acked.is_set():
            seq = sync_state.next_seq()
            payload = {
                "lease_token": lease_token,
                "sync_seq": seq,
                "status": sync_state.status,
                "exit_code": sync_state.exit_code,
                "message": sync_state.message,
            }

            try:
                result = await self.client.sync_job(job_id, payload)
                last_sync_seq = result.get("last_sync_seq", 0)
                if seq <= last_sync_seq and payload["status"] != "running":
                    sync_state.terminal_acked.set()
            except Exception as exc:
                logger.warning(
                    "job sync failed: %s",
                    exc,
                    extra={"job_id": job_id, "sync_seq": seq},
                )

            if sync_state.terminal_acked.is_set():
                break

            try:
                await asyncio.wait_for(sync_state.wake_event.wait(), timeout=interval)
                sync_state.wake_event.clear()
            except asyncio.TimeoutError:
                pass

    def _load_task_spec(self, task_dir: Path, task_id: int):
        task_toml = task_dir / "task.toml"
        if not task_toml.is_file():
            raise TaskFetchError(f"task {task_id} zip does not contain task.toml")
        return parse_task_spec(task_toml.read_bytes())

    async def _prepare_workspace(
        self,
        job_dir: Path,
        crate_name: str,
        version: str,
        job_logger,
        docker: DockerRunner,
        spec,
    ):
        if job_dir.exists():
            await docker.ensure_workspace_ownership(job_dir, spec.image, spec.workspace)
            await asyncio.to_thread(shutil.rmtree, job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)

        temp_dir = Path(
            tempfile.mkdtemp(prefix=f"_{job_dir.name}-", dir=job_dir.parent)
        )
        try:
            crate_file = temp_dir / f"{crate_name}-{version}.crate"
            job_logger.info(
                "crate download started",
                extra={
                    "job_dir": job_dir.name,
                    "crate_name": crate_name,
                    "version": version,
                },
            )
            await self.crate_downloader.download(crate_name, version, crate_file)
            job_logger.info(
                "crate download completed",
                extra={
                    "job_dir": job_dir.name,
                    "crate_name": crate_name,
                    "version": version,
                },
            )

            extract_dir = temp_dir / "extract"
            extract_dir.mkdir()
            job_logger.info(
                "crate extraction started",
                extra={
                    "job_dir": job_dir.name,
                    "crate_name": crate_name,
                    "version": version,
                },
            )
            await asyncio.to_thread(
                self._extract_and_move_crate,
                crate_file,
                extract_dir,
                job_dir,
                crate_name,
                version,
            )
            job_logger.info(
                "crate extraction completed",
                extra={
                    "job_dir": job_dir.name,
                    "crate_name": crate_name,
                    "version": version,
                },
            )
        finally:
            if temp_dir.exists():
                await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)

    def _extract_and_move_crate(
        self,
        crate_file: Path,
        temp_extract_dir: Path,
        job_dir: Path,
        crate_name: str,
        version: str,
    ) -> None:
        with tarfile.open(crate_file, "r:gz") as tar:
            tar.extractall(temp_extract_dir)

        inner_dir = temp_extract_dir / f"{crate_name}-{version}"
        source_dir = inner_dir if inner_dir.exists() else temp_extract_dir
        for item in source_dir.iterdir():
            shutil.move(str(item), str(job_dir))

    def _overlay_task_files(self, task_dir: Path, job_dir: Path) -> None:
        # Zip contents override crate files on name conflicts. task.toml is
        # metadata, and .sha256 is the TaskCache marker; neither belongs in
        # the job workspace.
        shutil.copytree(
            task_dir,
            job_dir,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("task.toml", ".sha256"),
        )

    async def _upload_artifacts(
        self,
        job_id: int,
        lease_token: str,
        job_dir: Path,
        artifacts,
        job_logger,
        job_ctx,
    ) -> None:
        uploaded = 0
        for rel_path in artifacts:
            source = job_dir / rel_path
            if not source.exists():
                job_logger.warning(
                    "artifact path missing, skipping: %s",
                    rel_path,
                    extra={**job_ctx, "path": rel_path},
                )
                continue
            if source.is_file():
                files = [source]
            else:
                files = sorted(p for p in source.rglob("*") if p.is_file())
            for file_path in files:
                remote_path = file_path.relative_to(job_dir).as_posix()
                try:
                    await self.client.upload_artifact(
                        job_id, lease_token, remote_path, file_path
                    )
                    uploaded += 1
                except Exception as exc:
                    # Artifact loss must not turn an otherwise finished job
                    # into a failure; log and continue with the rest.
                    job_logger.warning(
                        "artifact upload failed: %s (path=%s)",
                        exc,
                        remote_path,
                        extra={**job_ctx, "path": remote_path},
                    )
        job_logger.info(
            "artifacts upload finished",
            extra={**job_ctx, "uploaded_files": uploaded},
        )
