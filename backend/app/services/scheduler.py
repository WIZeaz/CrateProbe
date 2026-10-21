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
        running = self.db.get_jobs_by_status(TaskStatus.RUNNING)
        return len(running)

    async def schedule_tasks(self):
        self.reconcile_expired_leases()
        self.reconcile_stale_tasks()

    def reconcile_expired_leases(self):
        now = datetime.now()
        cursor = self.db.conn.cursor()
        cursor.execute(
            """
            UPDATE jobs
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
                "requeued running jobs with expired lease",
                extra={
                    "requeued_count": cursor.rowcount,
                    "from_status": TaskStatus.RUNNING.value,
                    "to_status": TaskStatus.PENDING.value,
                    "lease_cutoff_ts": now.isoformat(),
                },
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
        running_jobs = self.db.get_jobs_by_status(TaskStatus.RUNNING)
        for job in running_jobs:
            self.db.update_job_status(
                job.id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message="Job interrupted by server shutdown",
            )
            logger.info(
                "job marked as failed during shutdown cleanup",
                extra={
                    "job_id": job.id,
                    "crate_name": job.crate_name,
                    "reason": "server_shutdown",
                    "from_status": TaskStatus.RUNNING.value,
                    "to_status": TaskStatus.FAILED.value,
                },
            )

    def reconcile_stale_tasks(self):
        stale_count = self.db.reconcile_stale_jobs(
            self.config.max_state_sync_interval_seconds
        )
        if stale_count > 0:
            logger.warning(
                "marked stale jobs as runner_failed",
                extra={
                    "stale_count": stale_count,
                    "to_status": TaskStatus.RUNNER_FAILED.value,
                    "max_sync_interval_seconds": self.config.max_state_sync_interval_seconds,
                },
            )

    def recover_orphaned_tasks(self):
        running_jobs = self.db.get_jobs_by_status(TaskStatus.RUNNING)
        if not running_jobs:
            return
        logger.warning(
            "found orphaned running jobs on startup, marking as failed",
            extra={
                "orphaned_count": len(running_jobs),
                "from_status": TaskStatus.RUNNING.value,
                "to_status": TaskStatus.FAILED.value,
            },
        )
        for job in running_jobs:
            self.db.update_job_status(
                job.id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message="Job interrupted by server restart",
            )
            logger.info(
                "job marked as failed during orphan recovery",
                extra={
                    "job_id": job.id,
                    "crate_name": job.crate_name,
                    "from_status": TaskStatus.RUNNING.value,
                    "to_status": TaskStatus.FAILED.value,
                    "reason": "server_restart",
                },
            )

    async def cancel_task(self, job_id: int):
        job = self.db.get_job(job_id)
        if not job or job.status != TaskStatus.RUNNING:
            return
        self.db.update_job_status(
            job_id, TaskStatus.CANCELLED, finished_at=datetime.now()
        )
