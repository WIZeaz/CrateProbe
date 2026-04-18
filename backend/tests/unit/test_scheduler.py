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


def test_reconcile_expired_leases_logs_aggregate_warning_fields(scheduler, caplog):
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

    caplog.set_level("WARNING")
    scheduler.reconcile_expired_leases()

    record = next(r for r in caplog.records if "requeued" in r.message.lower())
    assert record.requeued_count == 1
    assert record.from_status == TaskStatus.RUNNING.value
    assert record.to_status == TaskStatus.PENDING.value
    assert record.lease_cutoff_ts


def test_recover_orphaned_tasks(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    scheduler.recover_orphaned_tasks()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.FAILED


def test_recover_orphaned_tasks_logs_per_task_context(scheduler, caplog):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    caplog.set_level("INFO")
    scheduler.recover_orphaned_tasks()

    record = next(r for r in caplog.records if "orphan recovery" in r.message.lower())
    assert record.task_id == task.id
    assert record.crate_name == task.crate_name
    assert record.from_status == TaskStatus.RUNNING.value
    assert record.to_status == TaskStatus.FAILED.value


def test_cleanup_remaining_tasks_logs_per_task_context(scheduler, caplog):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    caplog.set_level("INFO")
    scheduler._cleanup_remaining_tasks()

    record = next(r for r in caplog.records if "shutdown cleanup" in r.message.lower())
    assert record.task_id == task.id
    assert record.crate_name == task.crate_name
    assert record.reason == "server_shutdown"


def test_cancel_task(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    import asyncio

    asyncio.run(scheduler.cancel_task(task.id))
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.CANCELLED
