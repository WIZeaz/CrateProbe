import asyncio
import shutil
import tarfile
from pathlib import Path
from datetime import datetime
from typing import Tuple
from app.config import Config
from app.database import Database
from app.models import TaskStatus
from app.services.crates_api import CratesAPI
from app.utils.resource_limit import ResourceLimiter
from app.utils.docker_runner import DockerRunner


class TaskExecutor:
    """Executes individual tasks"""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.crates_api = CratesAPI()

        # Initialize appropriate runner based on execution mode
        self.execution_mode = getattr(config, "execution_mode", "systemd")
        self.stats_update_interval_seconds = 10

        if self.execution_mode == "docker":
            self.docker_runner = DockerRunner(
                image=config.docker_image,
                max_memory_gb=config.max_memory_gb,
                max_runtime_seconds=config.max_runtime_seconds,
                max_cpus=getattr(config, "max_cpus", 4),
                mounts=getattr(config, "docker_mounts", []),
            )
            self.limiter = None
        else:
            self.docker_runner = None
            self.limiter = ResourceLimiter(
                use_systemd=config.use_systemd,
                max_memory_gb=config.max_memory_gb,
                max_runtime_seconds=config.max_runtime_seconds,
            )

    async def prepare_workspace(
        self, task_id: int, crate_name: str, version: str
    ) -> Path:
        """Download and extract crate to workspace"""
        import logging

        task_logger = logging.getLogger(f"task.{task_id}")

        workspace_dir = self.config.workspace_path / "repos" / f"{crate_name}-{version}"

        # If workspace directory already exists (e.g., from retry), clean it first
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)

        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Download crate file
        crate_file = (
            self.config.workspace_path / "repos" / f"{crate_name}-{version}.crate"
        )

        # Remove old crate file if it exists
        if crate_file.exists():
            crate_file.unlink()

        await self.crates_api.download_crate(crate_name, version, str(crate_file))
        task_logger.info(f"[{task_id}] Crate downloaded successfully")

        # Extract crate - .crate files contain a top-level directory we need to strip
        temp_extract_dir = (
            self.config.workspace_path / "repos" / f"_temp_{crate_name}-{version}"
        )
        temp_extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            task_logger.info("Extracting crate archive...")
            with tarfile.open(crate_file, "r:gz") as tar:
                tar.extractall(temp_extract_dir)

            # Move contents from the inner directory to workspace_dir
            # .crate files have structure: crate-name-version/...
            inner_dir = temp_extract_dir / f"{crate_name}-{version}"
            if inner_dir.exists():
                # Move all contents from inner_dir to workspace_dir
                for item in inner_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
            else:
                # Fallback: if structure is different, move everything
                for item in temp_extract_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
            task_logger.info("Extraction complete")
        except Exception as e:
            task_logger.error(f"Extraction failed: {e}")
            raise
        finally:
            # Clean up temp directory
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

        # Remove crate file after extraction
        if crate_file.exists():
            crate_file.unlink()

        return workspace_dir

    async def execute_task(self, task_id: int):
        """Execute a single task"""
        import logging

        task = self.db.get_task(task_id)
        if not task:
            return

        # Set up per-task runner logger — first action, before status update or any branch
        runner_log_path = self.config.workspace_path / "logs" / f"{task_id}-runner.log"
        runner_log_path.parent.mkdir(parents=True, exist_ok=True)
        task_logger = logging.getLogger(f"task.{task_id}")
        task_logger.setLevel(logging.DEBUG)
        # Remove existing handlers to avoid duplicates on retry
        task_logger.handlers.clear()
        handler = logging.FileHandler(str(runner_log_path), mode="w")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        task_logger.addHandler(handler)

        try:
            task_logger.info(
                f"Task #{task_id} started: {task.crate_name} {task.version} "
                f"(mode={self.execution_mode})"
            )

            # Update status to running
            self.db.update_task_status(
                task_id, TaskStatus.RUNNING, started_at=datetime.now()
            )

            # Prepare workspace
            task_logger.info(f"Downloading crate {task.crate_name} {task.version}...")
            workspace_dir = await self.prepare_workspace(
                task_id, task.crate_name, task.version
            )
            task_logger.info(f"Workspace ready: {workspace_dir}")

            # Ensure log directory exists
            Path(task.stdout_log).parent.mkdir(parents=True, exist_ok=True)
            Path(task.stderr_log).parent.mkdir(parents=True, exist_ok=True)

            if self.execution_mode == "docker":
                task_logger.info("Checking Docker availability...")
                if not self.docker_runner.is_available():
                    msg = "Docker is not available but execution_mode is 'docker'"
                    task_logger.error(msg)
                    raise RuntimeError(msg)

                task_logger.info(
                    f"Ensuring Docker image: {self.config.docker_image} "
                    f"(policy={self.config.docker_pull_policy})"
                )
                if not self.docker_runner.ensure_image(self.config.docker_pull_policy):
                    msg = f"Docker image {self.config.docker_image} is not available"
                    task_logger.error(msg)
                    raise RuntimeError(msg)

                cmd = [
                    "cargo",
                    "rapx",
                    f"--test-crate={task.crate_name}",
                    "test",
                ]
                task_logger.info(f"Running command: {' '.join(cmd)}")
                task_logger.info(
                    "Starting Docker container (PID not available in Docker mode)..."
                )

                result = await self._run_docker_with_stats_updates(
                    task_id=task_id,
                    workspace_dir=workspace_dir,
                    command=cmd,
                    stdout_log=Path(task.stdout_log),
                    stderr_log=Path(task.stderr_log),
                )
                task_logger.info(f"Process exited with code: {result.exit_code}")

                # Final count of generated items
                case_count, poc_count = self.count_generated_items(workspace_dir)
                self.db.update_task_counts(task_id, case_count, poc_count)

                # Update final status
                self.db.update_task_status(
                    task_id,
                    result.state,
                    finished_at=datetime.now(),
                    exit_code=result.exit_code,
                    message=result.message,
                )
            else:
                # Set environment variables to force color output
                import os

                os.environ["CARGO_TERM_COLOR"] = "always"
                os.environ["TERM"] = "xterm-256color"

                # Use traditional execution with systemd/resource
                await self._execute_with_limiter(task_id, workspace_dir, task)

        except Exception as e:
            task_logger.error(f"Task failed with exception: {e}")
            self.db.update_task_status(
                task_id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message=str(e),
                message=f"Execution error: {e}",
            )
        finally:
            task_logger.info(f"Task #{task_id} runner log closed.")
            task_logger.removeHandler(handler)
            handler.close()

    async def _run_docker_with_stats_updates(
        self,
        task_id: int,
        workspace_dir: Path,
        command,
        stdout_log: Path,
        stderr_log: Path,
    ):
        """Run Docker task and periodically update generated item counts."""
        docker_run_task = asyncio.create_task(
            self.docker_runner.run(
                command=command,
                workspace_dir=workspace_dir,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )
        )

        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.shield(docker_run_task),
                    timeout=self.stats_update_interval_seconds,
                )
            except asyncio.TimeoutError:
                case_count, poc_count = self.count_generated_items(workspace_dir)
                self.db.update_task_counts(task_id, case_count, poc_count)

    async def _execute_with_limiter(self, task_id: int, workspace_dir: Path, task):
        """Execute task using systemd/resource limiter (original implementation)"""
        import logging

        task_logger = logging.getLogger(f"task.{task_id}")

        # Build command
        cmd = self.limiter.build_command(
            ["cargo", "rapx", f"--test-crate={task.crate_name}", "test"],
            cwd=str(workspace_dir),
        )
        task_logger.info(f"Running command: {' '.join(cmd)}")

        # Open log files
        stdout_log = open(task.stdout_log, "w")
        stderr_log = open(task.stderr_log, "w")

        # Start process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=stdout_log,
            stderr=stderr_log,
            cwd=workspace_dir,
            preexec_fn=(
                self.limiter.apply_resource_limits
                if self.limiter.get_limit_method().value == "resource"
                else None
            ),
        )
        task_logger.info(f"Process started with PID: {process.pid}")

        # Store PID
        self.db.update_task_pid(task_id, process.pid)

        # Wait for completion with periodic stats updates
        await self._wait_with_stats_updates(process, task_id, workspace_dir)

        stdout_log.close()
        stderr_log.close()

        task_logger.info(f"Process exited with code: {process.returncode}")

        # Final count of generated items
        case_count, poc_count = self.count_generated_items(workspace_dir)
        self.db.update_task_counts(task_id, case_count, poc_count)

        # Update final status based on exit code
        if process.returncode == 0:
            self.db.update_task_status(
                task_id,
                TaskStatus.COMPLETED,
                finished_at=datetime.now(),
                exit_code=process.returncode,
                message="Completed successfully",
            )
        elif process.returncode in (-9, 137):
            # SIGKILL (from OOM killer or systemd MemoryMax)
            self.db.update_task_status(
                task_id,
                TaskStatus.OOM,
                finished_at=datetime.now(),
                exit_code=process.returncode,
                message="Process killed by OOM killer (out of memory)",
            )
        elif process.returncode in (-24, -14):
            # SIGXCPU (CPU time limit) or SIGALRM (wall-clock timeout)
            self.db.update_task_status(
                task_id,
                TaskStatus.TIMEOUT,
                finished_at=datetime.now(),
                exit_code=process.returncode,
                message=f"Execution timed out after {self.config.max_runtime_seconds} seconds",
            )
        else:
            self.db.update_task_status(
                task_id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                exit_code=process.returncode,
                message=f"Process exited with code {process.returncode}",
            )

    async def _wait_with_stats_updates(
        self, process, task_id: int, workspace_dir: Path
    ):
        """Wait for process completion while periodically updating stats in database"""
        update_interval = 10  # Update every 10 seconds

        while True:
            try:
                # Wait for process with timeout
                await asyncio.wait_for(process.wait(), timeout=update_interval)
                # Process completed
                break
            except asyncio.TimeoutError:
                # Process still running, update stats
                case_count, poc_count = self.count_generated_items(workspace_dir)
                self.db.update_task_counts(task_id, case_count, poc_count)
                # Continue waiting

    def count_generated_items(self, workspace_dir: Path) -> Tuple[int, int]:
        """Count generated test cases and POCs"""
        # Now that we've fixed extraction, testgen should be directly in workspace_dir
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
