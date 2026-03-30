import os
import signal
import logging
import asyncio
from datetime import datetime
from typing import Set
from app.config import Config
from app.database import Database
from app.models import TaskStatus
from app.services.task_executor import TaskExecutor

logger = logging.getLogger(__name__)


class TaskScheduler:
    """Schedules and manages task execution"""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.executor = TaskExecutor(config, database)
        self.running_tasks: Set[int] = set()
        self._running_task_futures: Set[asyncio.Task] = set()
        self._shutdown_event = asyncio.Event()

    def get_running_count(self) -> int:
        """Get count of currently running tasks"""
        running = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        return len(running)

    async def schedule_tasks(self):
        """Schedule pending tasks if capacity available"""
        running_count = self.get_running_count()
        available_slots = self.config.max_jobs - running_count

        if available_slots <= 0:
            return

        # Check for and fix any stuck running tasks before scheduling new ones
        self._check_and_fix_stuck_tasks()

        # Get pending tasks
        pending = self.db.get_pending_tasks_ordered()

        # Start tasks up to available capacity
        for task in pending[:available_slots]:
            task_future = asyncio.create_task(self._execute_and_cleanup(task.id))
            self._running_task_futures.add(task_future)
            task_future.add_done_callback(self._running_task_futures.discard)

    async def _execute_and_cleanup(self, task_id: int):
        """Execute task and clean up tracking when done"""
        try:
            await self.executor.execute_task(task_id)
        except asyncio.CancelledError:
            logger.info(f"Task {task_id} execution was cancelled during shutdown")
            raise

    def _cleanup_remaining_tasks(self):
        """Mark any running tasks as failed during shutdown"""
        running_tasks = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        for task in running_tasks:
            self.db.update_task_status(
                task.id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message="Task interrupted by server shutdown",
            )
            logger.info(
                f"Task {task.id} ({task.crate_name}) marked as FAILED due to shutdown"
            )

    async def cancel_task(self, task_id: int):
        """Cancel a running task"""
        task = self.db.get_task(task_id)

        if not task or task.status != TaskStatus.RUNNING:
            return

        if task.pid:
            try:
                os.kill(task.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Process already ended

        self.db.update_task_status(
            task_id, TaskStatus.CANCELLED, finished_at=datetime.now()
        )

    async def run(self):
        """Main scheduler loop"""
        try:
            while not self._shutdown_event.is_set():
                await self.schedule_tasks()
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass  # Normal check interval
        except asyncio.CancelledError:
            logger.info(
                "Scheduler received shutdown signal, initiating graceful shutdown..."
            )
            self._shutdown_event.set()
            # Cancel all running task futures
            if self._running_task_futures:
                logger.info(
                    f"Cancelling {len(self._running_task_futures)} running task(s)..."
                )
                for task_future in list(self._running_task_futures):
                    task_future.cancel()
                # Wait for tasks to complete cancellation (with timeout)
                if self._running_task_futures:
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(
                                *self._running_task_futures, return_exceptions=True
                            ),
                            timeout=10.0,
                        )
                    except asyncio.TimeoutError:
                        logger.warning("Some tasks did not terminate within timeout")
            # Mark remaining running tasks as failed
            self._cleanup_remaining_tasks()
            logger.info("Scheduler shutdown complete")
            raise  # Re-raise CancelledError to properly propagate

    def recover_orphaned_tasks(self):
        """Recover orphaned RUNNING tasks on server restart.

        Tasks that were RUNNING when the server stopped cannot still be running
        (the process was killed with the server). Mark them as FAILED.
        """
        running_tasks = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        if not running_tasks:
            return

        logger.warning(
            f"Found {len(running_tasks)} orphaned RUNNING task(s) on startup, "
            f"marking as FAILED"
        )
        for task in running_tasks:
            self.db.update_task_status(
                task.id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message="Task interrupted by server restart",
            )
            logger.info(f"Task {task.id} ({task.crate_name}) marked as FAILED")

    def is_task_actually_running(self, task_id: int) -> bool:
        """Check if a task is actually running by verifying its PID.

        Args:
            task_id: The task ID to check

        Returns:
            True if the process is still running, False otherwise
        """
        task = self.db.get_task(task_id)
        if not task or not task.pid:
            return False

        try:
            # Signal 0 is used to check if process exists (no actual signal sent)
            os.kill(task.pid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    def _check_and_fix_stuck_tasks(self):
        """Check for tasks stuck in RUNNING state and fix them.

        A task is considered stuck if:
        1. It has a PID but the process no longer exists
        2. It has exceeded max_runtime_seconds by a significant margin
        """
        import time

        running_tasks = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        if not running_tasks:
            return

        max_runtime = getattr(self.config, "max_runtime_seconds", 86400)
        # Allow 10% grace period + 60 seconds buffer
        grace_period = max_runtime + 60
        now = datetime.now()

        for task in running_tasks:
            # Check if process still exists
            if task.pid and not self.is_task_actually_running(task.id):
                logger.warning(
                    f"Task {task.id} ({task.crate_name}) has PID {task.pid} "
                    f"but process is not running. Marking as FAILED."
                )
                self.db.update_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    finished_at=now,
                    error_message="Process terminated unexpectedly (process not found)",
                )
                continue

            # Check if task has exceeded max runtime
            if task.started_at:
                elapsed = (now - task.started_at).total_seconds()
                if elapsed > grace_period:
                    logger.warning(
                        f"Task {task.id} ({task.crate_name}) has been running for "
                        f"{elapsed:.0f}s, exceeding limit of {grace_period}s. "
                        f"Marking as TIMEOUT."
                    )
                    # Try to kill the process if it exists
                    if task.pid:
                        try:
                            os.kill(task.pid, signal.SIGKILL)
                        except (OSError, ProcessLookupError):
                            pass
                    self.db.update_task_status(
                        task.id,
                        TaskStatus.TIMEOUT,
                        finished_at=now,
                        exit_code=-1,
                        message=f"Task exceeded maximum runtime of {max_runtime}s",
                    )
