import asyncio
import contextlib
import logging
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple
from core.models import TaskStatus
from runner.client import RunnerControlClient
from runner.config import RunnerConfig
from runner.crate_downloader import CrateDownloader
from runner.crates_api import CratesAPI
from runner.docker_runner import DockerRunner
from runner.reporter import TaskReporter

logger = logging.getLogger(__name__)


LOG_UPLOAD_CONFIG = {
    "stdout": "chunk",
    "stderr": "chunk",
    "runner": "chunk",
    "miri_report": "chunk",
    "stats-yaml": "full",
}


class _SyncState:
    def __init__(self):
        self.status = "running"
        self.exit_code = None
        self.message = None
        self.case_count = None
        self.poc_count = None
        self.compile_failed = None
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
    ):
        self.config = config
        self.client = client
        self.crate_downloader = crate_downloader or CrateDownloader(
            crates_api=CratesAPI(
                user_agent=config.crates_io_user_agent,
            ),
            max_concurrent_downloads=config.crates_io_max_concurrent_downloads,
        )

    async def close(self):
        await self.crate_downloader.close()

    async def execute_claimed_task(self, claimed: dict) -> None:
        task_id = claimed["id"]
        lease_token = claimed["lease_token"]
        crate_name = claimed["crate_name"]
        crate_version = claimed["version"]

        # Each task gets its own DockerRunner to avoid shared-state issues
        # when running multiple containers concurrently.
        docker = DockerRunner(
            image=self.config.docker_image,
            max_memory_gb=self.config.max_memory_gb,
            max_runtime_seconds=self.config.max_runtime_seconds,
            max_cpus=self.config.max_cpus,
            mounts=self.config.docker_mounts,
            log_sync_interval_seconds=self.config.log_sync_interval_seconds,
        )

        workspace_dir = (
            Path(self.config.workspace_dir) / "repos" / f"{crate_name}-{crate_version}"
        )
        logs_dir = Path(self.config.workspace_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runner_log = logs_dir / f"{task_id}-runner.log"
        stdout_log = logs_dir / f"{task_id}-stdout.log"
        stderr_log = logs_dir / f"{task_id}-stderr.log"

        handler = logging.FileHandler(str(runner_log), mode="w")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        task_logger = logging.getLogger(f"task.{task_id}")
        task_logger.setLevel(logging.DEBUG)
        task_logger.handlers.clear()
        task_logger.addHandler(handler)

        task_ctx = {
            "task_id": task_id,
            "crate_name": crate_name,
            "version": crate_version,
        }

        sync_state = _SyncState()

        reporter = TaskReporter(
            client=self.client,
            task_id=task_id,
            lease_token=lease_token,
            log_paths={
                "stdout": stdout_log,
                "stderr": stderr_log,
                "runner": runner_log,
                "miri_report": workspace_dir / "testgen" / "miri_report.txt",
                "stats-yaml": workspace_dir / "testgen" / "stats.yaml",
            },
            workspace_dir=workspace_dir,
            log_flush_interval=self.config.log_flush_interval_seconds,
            upload_config=LOG_UPLOAD_CONFIG,
        )
        reporter_task = asyncio.create_task(reporter.run())

        sync_task = None

        terminal_status = None
        terminal_exit_code = None
        terminal_message = None
        terminal_counts = None

        try:
            task_logger.info("task started", extra=task_ctx)

            if not await docker.is_available():
                raise RuntimeError("Docker is not available")

            if not await docker.ensure_image(self.config.docker_pull_policy):
                raise RuntimeError(
                    f"Docker image {self.config.docker_image} is not available"
                )

            await self._prepare_workspace(
                workspace_dir, crate_name, crate_version, task_logger, docker
            )

            task_logger.info("workspace prepared, starting state sync", extra=task_ctx)
            sync_task = asyncio.create_task(
                self._state_sync_loop(
                    task_id,
                    lease_token,
                    workspace_dir,
                    self.config.state_sync_interval_seconds,
                    sync_state,
                )
            )

            cmd = ["cargo", "rapx", f"--test-crate={crate_name}", "test"]
            task_logger.info(
                "command started",
                extra={**task_ctx, "command_summary": " ".join(cmd)},
            )

            result = await docker.run(
                command=cmd,
                workspace_dir=workspace_dir,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )
            task_logger.info(
                "command finished",
                extra={**task_ctx, "exit_code": result.exit_code},
            )

            case_count, poc_count = await asyncio.to_thread(
                self._count_generated_items, workspace_dir
            )
            compile_failed = await asyncio.to_thread(
                self._get_compile_failed_count, workspace_dir
            )

            terminal_status = result.state.value
            terminal_exit_code = result.exit_code
            terminal_message = result.message
            terminal_counts = {
                "case_count": case_count,
                "poc_count": poc_count,
                "compile_failed": compile_failed,
            }
        except asyncio.CancelledError:
            task_logger.info("task cancelled", extra=task_ctx)
            terminal_status = "failed"
            terminal_message = "Task interrupted by shutdown"
            raise
        except Exception as exc:
            task_logger.exception("task execution failed", extra=task_ctx)
            terminal_status = "failed"
            terminal_message = str(exc)
        finally:
            await self._shutdown_task(
                reporter=reporter,
                reporter_task=reporter_task,
                sync_state=sync_state,
                sync_task=sync_task,
                terminal_status=terminal_status,
                terminal_exit_code=terminal_exit_code,
                terminal_message=terminal_message,
                terminal_counts=terminal_counts,
                task_logger=task_logger,
                handler=handler,
                docker=docker,
                task_ctx=task_ctx,
            )

    async def _shutdown_task(
        self,
        reporter,
        reporter_task,
        sync_state,
        sync_task,
        terminal_status,
        terminal_exit_code,
        terminal_message,
        terminal_counts,
        task_logger,
        handler,
        docker,
        task_ctx,
    ):
        reporter.stop()
        try:
            await asyncio.wait_for(reporter_task, timeout=30.0)
        except asyncio.TimeoutError:
            reporter_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reporter_task

        if terminal_status is not None:
            sync_state.set_terminal(
                status=terminal_status,
                exit_code=terminal_exit_code,
                message=terminal_message,
                **(terminal_counts or {}),
            )
            try:
                await asyncio.wait_for(sync_state.terminal_acked.wait(), timeout=300.0)
                task_logger.info("terminal sync acked", extra=task_ctx)
            except asyncio.TimeoutError:
                task_logger.warning(
                    "terminal sync not acked within timeout", extra=task_ctx
                )

        sync_state.terminal_acked.set()
        if sync_task is not None:
            try:
                await asyncio.wait_for(sync_task, timeout=10.0)
            except asyncio.TimeoutError:
                sync_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sync_task
        else:
            task_logger.info(
                "no state sync loop started, skipping terminal sync wait",
                extra=task_ctx,
            )

        task_logger.info("task runner log closed", extra=task_ctx)
        task_logger.removeHandler(handler)
        handler.close()
        await docker.close()

    async def _state_sync_loop(
        self,
        task_id: int,
        lease_token: str,
        workspace_dir: Path,
        interval: float,
        sync_state: _SyncState,
    ) -> None:
        while not sync_state.terminal_acked.is_set():
            seq = sync_state.next_seq()
            try:
                case_count, poc_count = await asyncio.to_thread(
                    self._count_generated_items, workspace_dir
                )
                compile_failed = await asyncio.to_thread(
                    self._get_compile_failed_count, workspace_dir
                )
            except Exception:
                case_count = poc_count = compile_failed = None

            payload_status = sync_state.status
            payload = {
                "lease_token": lease_token,
                "sync_seq": seq,
                "status": payload_status,
                "exit_code": sync_state.exit_code,
                "message": sync_state.message,
                "case_count": case_count,
                "poc_count": poc_count,
                "compile_failed": compile_failed,
            }

            try:
                result = await self.client.sync_task(task_id, payload)
                last_sync_seq = result.get("last_sync_seq", 0)
                if seq <= last_sync_seq and payload_status != "running":
                    sync_state.terminal_acked.set()
            except Exception as exc:
                logger.warning(
                    "task sync failed: %s",
                    exc,
                    extra={"task_id": task_id, "sync_seq": seq},
                )

            try:
                await asyncio.wait_for(sync_state.wake_event.wait(), timeout=interval)
                sync_state.wake_event.clear()
            except asyncio.TimeoutError:
                pass

    async def _prepare_workspace(
        self,
        workspace_dir: Path,
        crate_name: str,
        version: str,
        task_logger,
        docker: DockerRunner,
    ):
        if workspace_dir.exists():
            await docker.ensure_workspace_ownership(workspace_dir)
            await asyncio.to_thread(shutil.rmtree, workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        crate_file = workspace_dir.parent / f"{crate_name}-{version}.crate"
        crate_file.parent.mkdir(parents=True, exist_ok=True)
        if crate_file.exists():
            crate_file.unlink()

        task_logger.info(
            "crate download started",
            extra={"crate_name": crate_name, "version": version},
        )
        await self.crate_downloader.download(crate_name, version, crate_file)
        task_logger.info(
            "crate download completed",
            extra={"crate_name": crate_name, "version": version},
        )

        temp_extract_dir = workspace_dir.parent / f"_temp_{crate_name}-{version}"
        temp_extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            task_logger.info(
                "crate extraction started",
                extra={"crate_name": crate_name, "version": version},
            )
            await asyncio.to_thread(
                self._extract_and_move_crate,
                crate_file,
                temp_extract_dir,
                workspace_dir,
                crate_name,
                version,
            )
            task_logger.info(
                "crate extraction completed",
                extra={"crate_name": crate_name, "version": version},
            )
        finally:
            if temp_extract_dir.exists():
                await asyncio.to_thread(shutil.rmtree, temp_extract_dir)
        if crate_file.exists():
            await asyncio.to_thread(crate_file.unlink)

    def _extract_and_move_crate(
        self,
        crate_file: Path,
        temp_extract_dir: Path,
        workspace_dir: Path,
        crate_name: str,
        version: str,
    ) -> None:
        with tarfile.open(crate_file, "r:gz") as tar:
            tar.extractall(temp_extract_dir)

        inner_dir = temp_extract_dir / f"{crate_name}-{version}"
        source_dir = inner_dir if inner_dir.exists() else temp_extract_dir
        for item in source_dir.iterdir():
            shutil.move(str(item), str(workspace_dir))

    def _count_generated_items(self, workspace_dir: Path) -> Tuple[int, int]:
        testgen_dir = workspace_dir / "testgen"
        case_count = 0
        poc_count = 0
        tests_dir = testgen_dir / "tests"
        if tests_dir.exists():
            case_count = len([d for d in tests_dir.iterdir() if d.is_dir()])
        poc_dir = testgen_dir / "poc"
        if poc_dir.exists():
            poc_count = len([d for d in poc_dir.iterdir() if d.is_dir()])
        return case_count, poc_count

    def _get_compile_failed_count(self, workspace_dir: Path) -> int | None:
        stats_yaml_path = workspace_dir / "testgen" / "stats.yaml"
        if not stats_yaml_path.exists():
            return None
        try:
            lines = stats_yaml_path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
        except Exception:
            return None
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if not line.startswith("CompileFailed:") and not line.startswith(
                "compile_failed:"
            ):
                continue
            value = line.split(":", 1)[1].strip()
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1].strip()
            if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                value = value[1:-1].strip()
            if value.isdigit():
                return int(value)
            return None
        return None
