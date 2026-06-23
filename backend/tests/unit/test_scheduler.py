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


def test_reconcile_stale_tasks_marks_overdue_as_runner_failed(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_state_sync(task.id)
    # Manually backdate last_state_sync_at to exceed threshold
    db.conn.execute(
        "UPDATE tasks SET last_state_sync_at = ? WHERE id = ?",
        (datetime.now() - timedelta(seconds=150), task.id),
    )
    db.conn.commit()
    scheduler.reconcile_stale_tasks()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.RUNNER_FAILED
    assert updated.finished_at is not None
    assert updated.last_state_sync_at is None


def test_reconcile_stale_tasks_ignores_fresh_sync(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_state_sync(task.id)
    scheduler.reconcile_stale_tasks()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.RUNNING


def test_reconcile_stale_tasks_ignores_null_last_state_sync_at(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    # last_state_sync_at is NULL by default (backward compatibility)
    scheduler.reconcile_stale_tasks()
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.RUNNING


def test_reconcile_stale_tasks_logs_aggregate_warning_fields(scheduler, caplog):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_state_sync(task.id)
    db.conn.execute(
        "UPDATE tasks SET last_state_sync_at = ? WHERE id = ?",
        (datetime.now() - timedelta(seconds=150), task.id),
    )
    db.conn.commit()

    caplog.set_level("WARNING")
    scheduler.reconcile_stale_tasks()

    record = next(r for r in caplog.records if "stale" in r.message.lower())
    assert record.stale_count == 1
    assert record.to_status == TaskStatus.RUNNER_FAILED.value


def test_apply_task_sync_idempotent_by_seq(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    assert db.apply_task_sync(task.id, 1, "running") is True
    assert db.apply_task_sync(task.id, 1, "running") is False
    assert db.apply_task_sync(task.id, 2, "completed") is True
    assert db.apply_task_sync(task.id, 2, "completed") is False
    assert db.apply_task_sync(task.id, 1, "failed") is False


def test_apply_task_sync_prevents_terminal_to_running_rollback(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    db.apply_task_sync(task.id, 1, "completed")
    assert db.apply_task_sync(task.id, 2, "running") is False
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.COMPLETED


def test_apply_task_sync_allows_terminal_to_terminal_transition(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    db.apply_task_sync(task.id, 1, "runner_failed")
    assert db.apply_task_sync(task.id, 2, "completed") is True
    updated = db.get_task(task.id)
    assert updated.status == TaskStatus.COMPLETED


def test_apply_task_sync_updates_counts_on_running_sync(scheduler):
    db = scheduler.db
    db.create_task("serde", "1.0", "/path", "/log1", "/log2")
    task = db.get_tasks_by_status(TaskStatus.PENDING)[0]
    db.update_task_status(task.id, TaskStatus.RUNNING, started_at=datetime.now())

    db.apply_task_sync(
        task.id, 1, "running", case_count=5, poc_count=2, compile_failed=1
    )
    updated = db.get_task(task.id)
    assert updated.case_count == 5
    assert updated.poc_count == 2
    assert updated.compile_failed == 1
