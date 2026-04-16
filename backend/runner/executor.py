import asyncio
import logging
import shutil
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Tuple
from core.models import TaskStatus
from runner.client import RunnerControlClient
from runner.config import RunnerConfig
from runner.crates_api import CratesAPI
from runner.docker_runner import DockerRunner

logger = logging.getLogger(__name__)


class TaskExecutor:
    def __init__(self, config: RunnerConfig, client: RunnerControlClient):
        self.config = config
        self.client = client
        self.crates_api = CratesAPI()
        self.docker = DockerRunner(
            image=config.docker_image,
            max_memory_gb=config.max_memory_gb,
            max_runtime_seconds=config.max_runtime_seconds,
            max_cpus=config.max_cpus,
            mounts=config.docker_mounts,
        )

    async def close(self):
        await self.crates_api.close()

    async def execute_claimed_task(self, claimed: dict) -> None:
        task_id = claimed["id"]
        lease_token = claimed["lease_token"]
        crate_name = claimed["crate_name"]
        crate_version = claimed["version"]

        await self.client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
        )

        workspace_dir = (
            Path(self.config.workspace_dir) / f"{crate_name}-{crate_version}"
        )
        logs_dir = Path(self.config.workspace_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runner_log = logs_dir / f"{task_id}-runner.log"

        handler = logging.FileHandler(str(runner_log), mode="w")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        task_logger = logging.getLogger(f"task.{task_id}")
        task_logger.setLevel(logging.DEBUG)
        task_logger.handlers.clear()
        task_logger.addHandler(handler)

        try:
            task_logger.info(f"Task #{task_id} started: {crate_name} {crate_version}")

            if not self.docker.is_available():
                raise RuntimeError("Docker is not available")

            if not self.docker.ensure_image(self.config.docker_pull_policy):
                raise RuntimeError(
                    f"Docker image {self.config.docker_image} is not available"
                )

            await self._prepare_workspace(
                workspace_dir, crate_name, crate_version, task_logger
            )

            cmd = ["cargo", "rapx", f"--test-crate={crate_name}", "test"]
            task_logger.info(f"Running command: {' '.join(cmd)}")

            stdout_log = logs_dir / f"{task_id}-stdout.log"
            stderr_log = logs_dir / f"{task_id}-stderr.log"
            result = await self.docker.run(
                command=cmd,
                workspace_dir=workspace_dir,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )
            task_logger.info(f"Process exited with code: {result.exit_code}")

            case_count, poc_count = self._count_generated_items(workspace_dir)
            compile_failed = self._get_compile_failed_count(workspace_dir)

            await self._upload_logs(task_id, lease_token, workspace_dir)

            await self.client.send_event(
                task_id,
                {
                    "lease_token": lease_token,
                    "event_seq": 2,
                    "event_type": result.state.value,
                    "exit_code": result.exit_code,
                    "message": result.message,
                    "case_count": case_count,
                    "poc_count": poc_count,
                    "compile_failed": compile_failed,
                },
            )
        except asyncio.CancelledError:
            task_logger.info(f"Task #{task_id} cancelled")
            await self._upload_logs(task_id, lease_token, workspace_dir)
            await self.client.send_event(
                task_id,
                {
                    "lease_token": lease_token,
                    "event_seq": 2,
                    "event_type": "failed",
                    "message": "Task interrupted by shutdown",
                },
            )
            raise
        except Exception as e:
            task_logger.error(f"Task failed with exception: {e}")
            await self._upload_logs(task_id, lease_token, workspace_dir)
            await self.client.send_event(
                task_id,
                {
                    "lease_token": lease_token,
                    "event_seq": 2,
                    "event_type": "failed",
                    "message": str(e),
                },
            )
        finally:
            task_logger.info(f"Task #{task_id} runner log closed.")
            task_logger.removeHandler(handler)
            handler.close()

    async def _prepare_workspace(
        self, workspace_dir: Path, crate_name: str, version: str, task_logger
    ):
        if workspace_dir.exists():
            self.docker.ensure_workspace_ownership(workspace_dir)
            shutil.rmtree(workspace_dir)
        workspace_dir.mkdir(parents=True, exist_ok=True)

        crate_file = workspace_dir.parent / "repos" / f"{crate_name}-{version}.crate"
        crate_file.parent.mkdir(parents=True, exist_ok=True)
        if crate_file.exists():
            crate_file.unlink()

        task_logger.info(f"Downloading crate {crate_name} {version}...")
        await self.crates_api.download_crate(crate_name, version, crate_file)
        task_logger.info("Crate downloaded successfully")

        temp_extract_dir = (
            workspace_dir.parent / "repos" / f"_temp_{crate_name}-{version}"
        )
        temp_extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            task_logger.info("Extracting crate archive...")
            with tarfile.open(crate_file, "r:gz") as tar:
                tar.extractall(temp_extract_dir)
            inner_dir = temp_extract_dir / f"{crate_name}-{version}"
            if inner_dir.exists():
                for item in inner_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
            else:
                for item in temp_extract_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
            task_logger.info("Extraction complete")
        finally:
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)
        if crate_file.exists():
            crate_file.unlink()

    async def _upload_logs(self, task_id: int, lease_token: str, workspace_dir: Path):
        logs_dir = workspace_dir.parent / "logs"
        log_paths = [
            ("stdout", logs_dir / f"{task_id}-stdout.log"),
            ("stderr", logs_dir / f"{task_id}-stderr.log"),
            ("runner", logs_dir / f"{task_id}-runner.log"),
            ("miri_report", workspace_dir / "testgen" / "miri_report.txt"),
            ("stats-yaml", workspace_dir / "testgen" / "stats.yaml"),
        ]
        chunk_seq = 1
        for log_type, path in log_paths:
            if not path.exists():
                continue
            content = path.read_text(errors="replace")
            if not content:
                continue
            await self.client.send_log_chunk(
                task_id,
                log_type,
                {
                    "lease_token": lease_token,
                    "chunk_seq": chunk_seq,
                    "content": content,
                },
            )
            chunk_seq += 1

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
