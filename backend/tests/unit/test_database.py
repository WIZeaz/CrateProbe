"""Unit tests for database layer."""
import os
import tempfile
from datetime import datetime
import pytest

from app.database import Database, TaskRecord


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    database = Database(db_path)
    database.init_db()

    yield database

    database.close()
    os.unlink(db_path)


def test_database_initialization(db):
    """Test that database initializes correctly with tasks table."""
    # Verify the tasks table exists by trying to query it
    cursor = db.conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'")
    result = cursor.fetchone()
    assert result is not None
    assert result[0] == 'tasks'

    # Verify indexes exist
    cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
    indexes = [row[0] for row in cursor.fetchall()]
    assert 'idx_status' in indexes
    assert 'idx_created_at' in indexes


def test_create_task(db):
    """Test creating a task returns a valid ID."""
    task_id = db.create_task(
        title="Test Task",
        description="Test Description",
        case_count=5,
        poc_count=3
    )

    assert task_id is not None
    assert isinstance(task_id, int)
    assert task_id > 0


def test_get_task(db):
    """Test retrieving a task by ID."""
    # Create a task
    task_id = db.create_task(
        title="Test Task",
        description="Test Description",
        case_count=5,
        poc_count=3
    )

    # Retrieve it
    task = db.get_task(task_id)

    assert task is not None
    assert task.id == task_id
    assert task.title == "Test Task"
    assert task.description == "Test Description"
    assert task.status == "pending"
    assert task.case_count == 5
    assert task.poc_count == 3
    assert task.cases_completed == 0
    assert task.pocs_completed == 0
    assert task.pid is None
    assert task.error_message is None
    assert task.result_summary is None
    assert isinstance(task.created_at, datetime)
    assert task.started_at is None
    assert task.completed_at is None


def test_get_all_tasks(db):
    """Test getting all tasks in correct order (newest first)."""
    # Create multiple tasks
    id1 = db.create_task("Task 1", "Description 1", 1, 1)
    id2 = db.create_task("Task 2", "Description 2", 2, 2)
    id3 = db.create_task("Task 3", "Description 3", 3, 3)

    # Get all tasks
    tasks = db.get_all_tasks()

    assert len(tasks) == 3
    # Should be ordered by created_at DESC (newest first)
    assert tasks[0].id == id3
    assert tasks[1].id == id2
    assert tasks[2].id == id1


def test_update_task_status(db):
    """Test updating task status and related fields."""
    # Create a task
    task_id = db.create_task("Test Task", "Description", 5, 3)

    # Update to running
    db.update_task_status(task_id, "running")
    task = db.get_task(task_id)
    assert task.status == "running"
    assert task.started_at is not None

    # Update to completed with summary
    db.update_task_status(task_id, "completed", result_summary="All tests passed")
    task = db.get_task(task_id)
    assert task.status == "completed"
    assert task.completed_at is not None
    assert task.result_summary == "All tests passed"

    # Update to failed with error
    task_id2 = db.create_task("Task 2", "Description", 1, 1)
    db.update_task_status(task_id2, "failed", error_message="Connection timeout")
    task2 = db.get_task(task_id2)
    assert task2.status == "failed"
    assert task2.error_message == "Connection timeout"


def test_update_task_counts(db):
    """Test updating case and POC counts."""
    # Create a task
    task_id = db.create_task("Test Task", "Description", 10, 5)

    # Update counts
    db.update_task_counts(task_id, cases_completed=7, pocs_completed=3)
    task = db.get_task(task_id)

    assert task.cases_completed == 7
    assert task.pocs_completed == 3


def test_get_tasks_by_status(db):
    """Test filtering tasks by status."""
    # Create tasks with different statuses
    id1 = db.create_task("Task 1", "Description", 1, 1)
    id2 = db.create_task("Task 2", "Description", 2, 2)
    id3 = db.create_task("Task 3", "Description", 3, 3)

    db.update_task_status(id1, "running")
    db.update_task_status(id2, "completed")
    # id3 remains pending

    # Get pending tasks
    pending = db.get_tasks_by_status("pending")
    assert len(pending) == 1
    assert pending[0].id == id3

    # Get running tasks
    running = db.get_tasks_by_status("running")
    assert len(running) == 1
    assert running[0].id == id1

    # Get completed tasks
    completed = db.get_tasks_by_status("completed")
    assert len(completed) == 1
    assert completed[0].id == id2
