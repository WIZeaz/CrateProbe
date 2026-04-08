import pytest
import asyncio
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, AsyncMock
from app.services.scheduler import TaskScheduler
from app.database import Database
from app.models import TaskStatus
from app.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(workspace_path=tmp_path / "workspace", max_jobs=2)


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    database.init_db()
    return database


@pytest.fixture
def scheduler(config, db):
    return TaskScheduler(config, db)


@pytest.mark.asyncio
async def test_scheduler_starts_pending_tasks(scheduler, db):
    """Test scheduler picks up pending tasks"""
    # Create pending tasks
    db.create_task("serde", "1.0.0", "/path1", "/log1", "/log2")
    db.create_task("tokio", "1.0.0", "/path2", "/log3", "/log4")

    with patch.object(
        scheduler, "_execute_and_cleanup", new_callable=AsyncMock
    ) as mock_exec:
        # Run one scheduling cycle
        await scheduler.schedule_tasks()

        # Should have started both tasks (max_jobs=2)
        assert mock_exec.call_count == 2


@pytest.mark.asyncio
async def test_scheduler_respects_max_jobs(scheduler, db, config):
    """Test scheduler respects max_jobs limit"""
    # Create 3 pending tasks but max_jobs=2
    id1 = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    id2 = db.create_task("crate2", "1.0.0", "/path2", "/log3", "/log4")
    id3 = db.create_task("crate3", "1.0.0", "/path3", "/log5", "/log6")

    # Simulate two already running
    db.update_task_status(id1, TaskStatus.RUNNING)
    db.update_task_status(id2, TaskStatus.RUNNING)

    with patch.object(
        scheduler.executor, "execute_task", new_callable=AsyncMock
    ) as mock_exec:
        await scheduler.schedule_tasks()

        # Should not start any new tasks (at capacity)
        assert mock_exec.call_count == 0


def test_get_running_count(scheduler, db):
    """Test counting running tasks"""
    id1 = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    id2 = db.create_task("crate2", "1.0.0", "/path2", "/log3", "/log4")

    db.update_task_status(id1, TaskStatus.RUNNING)

    assert scheduler.get_running_count() == 1


@pytest.mark.asyncio
async def test_cancel_task(scheduler, db):
    """Test canceling a running task"""
    task_id = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    db.update_task_status(task_id, TaskStatus.RUNNING)
    db.update_task_pid(task_id, 12345)

    with patch("os.kill") as mock_kill:
        await scheduler.cancel_task(task_id)

        mock_kill.assert_called_once()
        task = db.get_task(task_id)
        assert task.status == TaskStatus.CANCELLED


def test_recover_orphaned_running_tasks(scheduler, db):
    """Test recovery of orphaned RUNNING tasks on server restart"""
    # Create tasks in RUNNING state (simulating server crash/shutdown)
    task_id1 = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    task_id2 = db.create_task("crate2", "1.0.0", "/path2", "/log3", "/log4")
    db.update_task_status(task_id1, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_status(task_id2, TaskStatus.RUNNING, started_at=datetime.now())

    # Verify tasks are running
    assert scheduler.get_running_count() == 2

    # Call recovery method
    scheduler.recover_orphaned_tasks()

    # Verify tasks are now marked as failed with appropriate message
    task1 = db.get_task(task_id1)
    task2 = db.get_task(task_id2)

    assert task1.status == TaskStatus.FAILED
    assert task1.error_message == "Task interrupted by server restart"
    assert task1.finished_at is not None

    assert task2.status == TaskStatus.FAILED
    assert task2.error_message == "Task interrupted by server restart"
    assert task2.finished_at is not None

    # Verify running count is now 0
    assert scheduler.get_running_count() == 0


@pytest.mark.asyncio
async def test_expired_lease_requeues_running_task_in_distributed_mode(scheduler, db):
    """Expired distributed lease should requeue task to pending."""
    scheduler.config.distributed_enabled = True

    task_id = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())
    db.conn.execute(
        """
        UPDATE tasks
        SET runner_id = ?, lease_token = ?, lease_expires_at = ?, attempt = ?
        WHERE id = ?
        """,
        (
            "runner-1",
            "lease-token-1",
            datetime.now() - timedelta(seconds=10),
            2,
            task_id,
        ),
    )
    db.conn.commit()

    with patch.object(
        scheduler, "_execute_and_cleanup", new_callable=AsyncMock
    ) as mock_exec:
        await scheduler.schedule_tasks()
        assert mock_exec.call_count == 1

    task = db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.runner_id is None
    assert task.lease_token is None
    assert task.lease_expires_at is None
    assert task.attempt == 3


@pytest.mark.asyncio
async def test_distributed_reconciliation_skipped_when_disabled(scheduler, db):
    """Local mode should not requeue tasks based on lease fields."""
    task_id = db.create_task("crate1", "1.0.0", "/path1", "/log1", "/log2")
    db.update_task_status(task_id, TaskStatus.RUNNING)
    expired = datetime.now() - timedelta(seconds=10)
    db.conn.execute(
        """
        UPDATE tasks
        SET runner_id = ?, lease_token = ?, lease_expires_at = ?, attempt = ?
        WHERE id = ?
        """,
        ("runner-1", "lease-token-1", expired, 2, task_id),
    )
    db.conn.commit()

    with patch.object(
        scheduler, "_execute_and_cleanup", new_callable=AsyncMock
    ) as mock_exec:
        await scheduler.schedule_tasks()
        assert mock_exec.call_count == 0

    task = db.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.runner_id == "runner-1"
    assert task.lease_token == "lease-token-1"
    assert task.lease_expires_at == expired
    assert task.attempt == 2
