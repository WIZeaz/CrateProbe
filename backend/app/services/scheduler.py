import logging
import asyncio
from datetime import datetime
from app.config import Config
from app.database import Database
from core.models import TaskStatus

logger = logging.getLogger(__name__)


class TaskScheduler:
    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self._shutdown_event = asyncio.Event()

    def get_running_count(self) -> int:
        running = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        return len(running)

    async def schedule_tasks(self):
        self.reconcile_expired_leases()

    def reconcile_expired_leases(self):
        now = datetime.now()
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE tasks
            SET status = ?,
                runner_id = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                attempt = COALESCE(attempt, 0) + 1
            WHERE status = ?
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at <= ?
            """,
            (TaskStatus.PENDING.value, TaskStatus.RUNNING.value, now),
        )
        self.db.conn.commit()
        if cursor.rowcount > 0:
            logger.warning(
                "Requeued %s running task(s) with expired lease", cursor.rowcount
            )

    async def run(self):
        try:
            while not self._shutdown_event.is_set():
                await self.schedule_tasks()
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            logger.info("Scheduler received shutdown signal, cleaning up...")
            self._shutdown_event.set()
            self._cleanup_remaining_tasks()
            logger.info("Scheduler shutdown complete")
            raise

    def _cleanup_remaining_tasks(self):
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

    def recover_orphaned_tasks(self):
        running_tasks = self.db.get_tasks_by_status(TaskStatus.RUNNING)
        if not running_tasks:
            return
        logger.warning(
            f"Found {len(running_tasks)} orphaned RUNNING task(s) on startup, marking as FAILED"
        )
        for task in running_tasks:
            self.db.update_task_status(
                task.id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message="Task interrupted by server restart",
            )
            logger.info(f"Task {task.id} ({task.crate_name}) marked as FAILED")

    async def cancel_task(self, task_id: int):
        task = self.db.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return
        self.db.update_task_status(
            task_id, TaskStatus.CANCELLED, finished_at=datetime.now()
        )
