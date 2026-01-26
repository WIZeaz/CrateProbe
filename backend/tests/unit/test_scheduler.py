import pytest
import asyncio
from unittest.mock import Mock, patch, AsyncMock
from app.services.scheduler import TaskScheduler
from app.database import Database
from app.models import TaskStatus
from app.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        workspace_path=tmp_path / "workspace",
        max_jobs=2
    )


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

    with patch.object(scheduler.executor, "execute_task", new_callable=AsyncMock) as mock_exec:
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

    with patch.object(scheduler.executor, "execute_task", new_callable=AsyncMock) as mock_exec:
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
