import pytest
from datetime import datetime, timedelta
from app.services.scheduler import TaskScheduler
from app.config import Config
from app.database import Database
from core.models import TaskStatus


@pytest.fixture
def scheduler(tmp_path):
    cfg = Config(workspace_path=tmp_path, db_path="test.db")
    db = Database(str(cfg.get_db_full_path()))
    db.init_db()
    return TaskScheduler(cfg, db)


def test_reconcile_expired_leases(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    db.conn.execute(
        """
        UPDATE tasks
        SET runner_id = ?, lease_token = ?, lease_expires_at = ?
        WHERE id = ?
        """,
        ("r1", "tok", datetime.now() - timedelta(seconds=1), task.id),
    )
    db.conn.commit()
    scheduler.reconcile_expired_leases()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.PENDING
    assert updated.runner_id is None


def test_recover_orphaned_tasks(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    scheduler.recover_orphaned_tasks()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.FAILED


def test_cancel_task(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    import asyncio

    asyncio.run(scheduler.cancel_task(task.id))
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.CANCELLED
