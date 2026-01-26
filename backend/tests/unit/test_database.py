import pytest
from datetime import datetime
from pathlib import Path
from app.database import Database, TaskRecord
from app.models import TaskStatus


@pytest.fixture
def db(tmp_path):
    """Create a test database"""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    database.init_db()
    return database


def test_database_initialization(tmp_path):
    """Test database file creation and table initialization"""
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    db.init_db()

    assert Path(db_path).exists()


def test_create_task(db):
    """Test creating a new task"""
    task_id = db.create_task(
        crate_name="serde",
        version="1.0.193",
        workspace_path="/tmp/workspace/repos/serde-1.0.193",
        stdout_log="/tmp/workspace/logs/1-stdout.log",
        stderr_log="/tmp/workspace/logs/1-stderr.log"
    )

    assert task_id == 1


def test_get_task(db):
    """Test retrieving a task by ID"""
    task_id = db.create_task(
        crate_name="serde",
        version="1.0.193",
        workspace_path="/tmp/workspace/repos/serde-1.0.193",
        stdout_log="/tmp/workspace/logs/1-stdout.log",
        stderr_log="/tmp/workspace/logs/1-stderr.log"
    )

    task = db.get_task(task_id)

    assert task is not None
    assert task.id == task_id
    assert task.crate_name == "serde"
    assert task.version == "1.0.193"
    assert task.status == TaskStatus.PENDING
    assert task.created_at is not None


def test_get_all_tasks(db):
    """Test retrieving all tasks"""
    db.create_task("serde", "1.0.0", "/path1", "/log1", "/log2")
    db.create_task("tokio", "1.35.0", "/path2", "/log3", "/log4")

    tasks = db.get_all_tasks()

    assert len(tasks) == 2
    assert tasks[0].crate_name == "tokio"  # Latest first
    assert tasks[1].crate_name == "serde"


def test_update_task_status(db):
    """Test updating task status"""
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())
    task = db.get_task(task_id)

    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None


def test_update_task_counts(db):
    """Test updating case and POC counts"""
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    db.update_task_counts(task_id, case_count=10, poc_count=3)
    task = db.get_task(task_id)

    assert task.case_count == 10
    assert task.poc_count == 3


def test_get_tasks_by_status(db):
    """Test filtering tasks by status"""
    id1 = db.create_task("serde", "1.0.0", "/path1", "/log1", "/log2")
    id2 = db.create_task("tokio", "1.0.0", "/path2", "/log3", "/log4")

    db.update_task_status(id1, TaskStatus.RUNNING)

    running_tasks = db.get_tasks_by_status(TaskStatus.RUNNING)
    pending_tasks = db.get_tasks_by_status(TaskStatus.PENDING)

    assert len(running_tasks) == 1
    assert running_tasks[0].id == id1
    assert len(pending_tasks) == 1
    assert pending_tasks[0].id == id2


def test_update_task_pid(db):
    """Test updating task PID"""
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    db.update_task_pid(task_id, pid=12345)
    task = db.get_task(task_id)

    assert task.pid == 12345


def test_reset_task_for_retry(db):
    """Test resetting task state for retry"""
    from datetime import datetime
    from app.models import TaskStatus

    # Create and complete a task
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    # Simulate task execution
    db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_pid(task_id, 12345)
    db.update_task_counts(task_id, case_count=10, poc_count=5)
    db.update_task_status(
        task_id,
        TaskStatus.COMPLETED,
        finished_at=datetime.now(),
        exit_code=0
    )

    # Verify task is completed
    task = db.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.case_count == 10
    assert task.poc_count == 5
    assert task.exit_code == 0
    assert task.pid == 12345
    assert task.started_at is not None
    assert task.finished_at is not None

    # Reset task for retry
    db.reset_task_for_retry(task_id)

    # Verify task is reset
    task = db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.case_count == 0
    assert task.poc_count == 0
    assert task.exit_code is None
    assert task.pid is None
    assert task.started_at is None
    assert task.finished_at is None
    assert task.error_message is None

    # Verify crate info is preserved
    assert task.crate_name == "serde"
    assert task.version == "1.0.0"
    assert task.workspace_path == "/path"
