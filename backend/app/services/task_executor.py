import asyncio
import tarfile
from pathlib import Path
from datetime import datetime
from typing import Tuple
from app.config import Config
from app.database import Database
from app.models import TaskStatus
from app.services.crates_api import CratesAPI
from app.utils.resource_limit import ResourceLimiter


class TaskExecutor:
    """Executes individual tasks"""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.crates_api = CratesAPI()
        self.limiter = ResourceLimiter(
            use_systemd=config.use_systemd,
            max_memory_gb=config.max_memory_gb,
            max_runtime_hours=config.max_runtime_hours
        )

    async def prepare_workspace(self, task_id: int, crate_name: str, version: str) -> Path:
        """Download and extract crate to workspace"""
        workspace_dir = self.config.workspace_path / "repos" / f"{crate_name}-{version}"
        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Download crate file
        crate_file = self.config.workspace_path / "repos" / f"{crate_name}-{version}.crate"
        await self.crates_api.download_crate(crate_name, version, str(crate_file))

        # Extract crate
        with tarfile.open(crate_file, "r:gz") as tar:
            tar.extractall(workspace_dir)

        # Remove crate file after extraction
        if crate_file.exists():
            crate_file.unlink()

        return workspace_dir

    async def execute_task(self, task_id: int):
        """Execute a single task"""
        task = self.db.get_task(task_id)
        if not task:
            return

        try:
            # Update status to running
            self.db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())

            # Prepare workspace
            workspace_dir = await self.prepare_workspace(task_id, task.crate_name, task.version)

            # Build command
            cmd = self.limiter.build_command(
                ["cargo", "rapx", "-testgen", f"-test-crate={task.crate_name}"],
                cwd=str(workspace_dir)
            )

            # Ensure log directory exists
            Path(task.stdout_log).parent.mkdir(parents=True, exist_ok=True)
            Path(task.stderr_log).parent.mkdir(parents=True, exist_ok=True)

            # Open log files
            stdout_log = open(task.stdout_log, "w")
            stderr_log = open(task.stderr_log, "w")

            # Start process
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=stdout_log,
                stderr=stderr_log,
                cwd=workspace_dir,
                preexec_fn=self.limiter.apply_resource_limits if self.limiter.get_limit_method().value == "resource" else None
            )

            # Store PID
            self.db.update_task_pid(task_id, process.pid)

            # Wait for completion
            await process.wait()

            stdout_log.close()
            stderr_log.close()

            # Count generated items
            case_count, poc_count = self.count_generated_items(workspace_dir)
            self.db.update_task_counts(task_id, case_count, poc_count)

            # Update final status
            if process.returncode == 0:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.COMPLETED,
                    finished_at=datetime.now(),
                    exit_code=process.returncode
                )
            else:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.FAILED,
                    finished_at=datetime.now(),
                    exit_code=process.returncode
                )

        except Exception as e:
            self.db.update_task_status(
                task_id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message=str(e)
            )

    def count_generated_items(self, workspace_dir: Path) -> Tuple[int, int]:
        """Count generated test cases and POCs"""
        # First try looking in workspace_dir/crate-name/testgen
        testgen_dir = workspace_dir / f"{workspace_dir.name}" / "testgen"

        # If that doesn't exist, try workspace_dir/testgen (for tests)
        if not testgen_dir.exists():
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
