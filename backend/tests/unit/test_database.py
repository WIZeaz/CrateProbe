import pytest
from datetime import datetime
from pathlib import Path
from app.database import Database, TaskRecord
from core.models import TaskStatus


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
        stderr_log="/tmp/workspace/logs/1-stderr.log",
    )

    assert task_id == 1


def test_get_task(db):
    """Test retrieving a task by ID"""
    task_id = db.create_task(
        crate_name="serde",
        version="1.0.193",
        workspace_path="/tmp/workspace/repos/serde-1.0.193",
        stdout_log="/tmp/workspace/logs/1-stdout.log",
        stderr_log="/tmp/workspace/logs/1-stderr.log",
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


def test_update_task_compile_failed(db):
    """Test updating compile_failed count"""
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    db.update_task_compile_failed(task_id, compile_failed=7)
    task = db.get_task(task_id)

    assert task.compile_failed == 7


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
    from core.models import TaskStatus

    # Create and complete a task
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    # Simulate task execution
    db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_task_pid(task_id, 12345)
    db.update_task_counts(task_id, case_count=10, poc_count=5)
    db.update_task_compile_failed(task_id, compile_failed=2)
    db.update_task_status(
        task_id, TaskStatus.COMPLETED, finished_at=datetime.now(), exit_code=0
    )

    # Verify task is completed
    task = db.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.case_count == 10
    assert task.poc_count == 5
    assert task.exit_code == 0
    assert task.compile_failed == 2
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
    assert task.compile_failed is None
    assert task.pid is None
    assert task.started_at is None
    assert task.finished_at is None
    assert task.error_message is None

    # Verify crate info is preserved
    assert task.crate_name == "serde"
    assert task.version == "1.0.0"
    assert task.workspace_path == "/path"


def test_reset_task_for_retry_resets_log_chunk_sequence(db):
    """Retry reset should allow ingesting chunk_seq from 1 again."""
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")

    assert db.record_task_log_chunk(task_id, "stdout", 1) is True
    assert db.record_task_log_chunk(task_id, "stdout", 1) is False

    db.reset_task_for_retry(task_id)

    assert db.record_task_log_chunk(task_id, "stdout", 1) is True


def test_get_task_by_crate_and_version_returns_task(db, tmp_path):
    """Test retrieving task by crate name and version"""
    db.init_db()

    # Create a task
    task_id = db.create_task(
        crate_name="test-crate",
        version="1.0.0",
        workspace_path=str(tmp_path / "workspace"),
        stdout_log=str(tmp_path / "stdout.log"),
        stderr_log=str(tmp_path / "stderr.log"),
    )

    # Retrieve by crate and version
    task = db.get_task_by_crate_and_version("test-crate", "1.0.0")

    assert task is not None
    assert task.id == task_id
    assert task.crate_name == "test-crate"
    assert task.version == "1.0.0"


def test_get_task_by_crate_and_version_returns_none_when_not_found(db, tmp_path):
    """Test retrieving non-existent task returns None"""
    db.init_db()

    # Try to retrieve non-existent task
    task = db.get_task_by_crate_and_version("non-existent", "1.0.0")

    assert task is None


def test_get_task_by_crate_and_version_requires_exact_match(db, tmp_path):
    """Test that crate name and version must match exactly"""
    db.init_db()

    # Create a task
    db.create_task(
        crate_name="test-crate",
        version="1.0.0",
        workspace_path=str(tmp_path / "workspace"),
        stdout_log=str(tmp_path / "stdout.log"),
        stderr_log=str(tmp_path / "stderr.log"),
    )

    # Different crate name
    assert db.get_task_by_crate_and_version("other-crate", "1.0.0") is None

    # Different version
    assert db.get_task_by_crate_and_version("test-crate", "2.0.0") is None


def test_init_db_creates_runners_table(db):
    """Test runners table exists with required columns"""
    cursor = db.conn.cursor()
    columns = {
        row["name"] for row in cursor.execute("PRAGMA table_info(runners)").fetchall()
    }

    assert "runner_id" in columns
    assert "token_hash" in columns
    assert "token_salt" in columns
    assert "enabled" in columns
    assert "last_seen_at" in columns


def test_tasks_table_has_distributed_columns(db):
    """Test tasks table has required distributed scheduling columns"""
    cursor = db.conn.cursor()
    columns = {
        row["name"] for row in cursor.execute("PRAGMA table_info(tasks)").fetchall()
    }

    assert "runner_id" in columns
    assert "lease_token" in columns
    assert "lease_expires_at" in columns
    assert "attempt" in columns
    assert "last_event_seq" in columns
    assert "cancel_requested" in columns


def test_create_and_disable_runner(db):
    """Test creating, reading, and disabling a runner"""
    created = db.create_runner(
        runner_id="runner-1",
        token_hash="hash-123",
        token_salt="salt-123",
    )

    assert created.runner_id == "runner-1"
    assert created.token_hash == "hash-123"
    assert created.token_salt == "salt-123"
    assert created.enabled is True

    fetched = db.get_runner_by_runner_id("runner-1")
    assert fetched is not None
    assert fetched.runner_id == "runner-1"
    assert fetched.enabled is True

    disabled = db.disable_runner("runner-1")
    assert disabled is True

    fetched_after_disable = db.get_runner_by_runner_id("runner-1")
    assert fetched_after_disable is not None
    assert fetched_after_disable.enabled is False


def test_enable_runner_sets_enabled_true_after_disable(db):
    """Test enabling a runner after disabling it"""
    db.create_runner(
        runner_id="runner-1",
        token_hash="hash-123",
        token_salt="salt-123",
    )

    assert db.disable_runner("runner-1") is True
    assert db.enable_runner("runner-1") is True

    fetched = db.get_runner_by_runner_id("runner-1")
    assert fetched is not None
    assert fetched.enabled is True


def test_delete_runner_removes_record(db):
    """Test deleting a runner removes it from database"""
    db.create_runner(
        runner_id="runner-1",
        token_hash="hash-123",
        token_salt="salt-123",
    )

    assert db.delete_runner("runner-1") is True
    assert db.get_runner_by_runner_id("runner-1") is None


def test_disable_and_enable_runner_are_idempotent_for_existing_runner(db):
    """Test disable and enable return True for existing runner, even when unchanged"""
    db.create_runner(
        runner_id="runner-1",
        token_hash="hash-123",
        token_salt="salt-123",
    )

    assert db.disable_runner("runner-1") is True
    assert db.disable_runner("runner-1") is True

    assert db.enable_runner("runner-1") is True
    assert db.enable_runner("runner-1") is True


def test_runner_mutations_return_false_for_missing_runner(db):
    """Test disable, enable, and delete return False for missing runner"""
    assert db.disable_runner("missing-runner") is False
    assert db.enable_runner("missing-runner") is False
    assert db.delete_runner("missing-runner") is False


def test_apply_task_event_started_sets_running(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    result = db.apply_task_event(task_id, 1, "started")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None
    assert task.finished_at is None


def test_apply_task_event_progress_sets_running(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "progress")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.finished_at is None


def test_apply_task_event_completed_sets_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "completed")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.finished_at is not None


def test_apply_task_event_failed_sets_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "failed")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert task.finished_at is not None


def test_apply_task_event_unknown_type_treats_as_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "garbage")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert task.finished_at is not None


def test_apply_task_event_timeout_sets_timeout_status(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "timeout")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.TIMEOUT
    assert task.finished_at is not None


def test_apply_task_event_oom_sets_oom_status(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "oom")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.OOM
    assert task.finished_at is not None


def test_apply_task_event_cancelled_sets_cancelled_status(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "cancelled")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.CANCELLED
    assert task.finished_at is not None
