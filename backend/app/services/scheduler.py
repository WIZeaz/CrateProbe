import os
import signal
import asyncio
from datetime import datetime
from typing import Set
from app.config import Config
from app.database import Database
from app.models import TaskStatus
from app.services.task_executor import TaskExecutor


class TaskScheduler:
    """Schedules and manages task execution"""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.executor = TaskExecutor(config, database)
        self.running_tasks: Set[int] = set()

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

        # Get pending tasks
        pending = self.db.get_tasks_by_status(TaskStatus.PENDING)

        # Start tasks up to available capacity
        for task in pending[:available_slots]:
            asyncio.create_task(self.executor.execute_task(task.id))

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
            task_id,
            TaskStatus.CANCELLED,
            finished_at=datetime.now()
        )

    async def run(self):
        """Main scheduler loop"""
        while True:
            await self.schedule_tasks()
            await asyncio.sleep(5)  # Check every 5 seconds
