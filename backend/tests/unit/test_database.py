import pytest
import itertools
from datetime import datetime, timedelta
from pathlib import Path
from app.database import Database, JobRecord, TaskDefRecord
from core.models import TaskStatus


@pytest.fixture
def db(tmp_path):
    """Create a test database"""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    database.init_db()
    return database


_sha_counter = itertools.count(1)


def make_task_definition(db, name="cargo-rapx", sha256=None, desc="run rapx"):
    """Create a task definition row and return its id."""
    sha256 = sha256 if sha256 is not None else f"sha-{name}-{next(_sha_counter)}"
    return db.create_task_definition(
        name=name,
        desc=desc,
        sha256=sha256,
        zip_path=f"/tmp/{sha256}.zip",
        spec="{}",
    )


def make_job(
    db,
    crate_name="serde",
    version="1.0.0",
    task_id=None,
    task_name="cargo-rapx",
    task_sha256="sha-cargo-rapx",
    task_spec="{}",
):
    """Create a job row (with a backing task definition) and return its id."""
    if task_id is None:
        task_id = make_task_definition(db)
    return db.create_job(
        crate_name=crate_name,
        version=version,
        task_id=task_id,
        task_name=task_name,
        task_sha256=task_sha256,
        task_spec=task_spec,
    )


def test_database_initialization(tmp_path):
    """Test database file creation and table initialization"""
    db_path = tmp_path / "test.db"
    db = Database(str(db_path))
    db.init_db()

    assert Path(db_path).exists()


def test_create_and_get_task_definition(db):
    """Test creating and reading a task definition"""
    task_id = db.create_task_definition(
        name="cargo-rapx",
        desc="run cargo rapx",
        sha256="abc123",
        zip_path="/tmp/abc123.zip",
        spec='{"name": "cargo-rapx"}',
    )

    assert task_id == 1

    record = db.get_task_definition(task_id)
    assert record is not None
    assert isinstance(record, TaskDefRecord)
    assert record.id == task_id
    assert record.name == "cargo-rapx"
    assert record.desc == "run cargo rapx"
    assert record.sha256 == "abc123"
    assert record.zip_path == "/tmp/abc123.zip"
    assert record.spec == '{"name": "cargo-rapx"}'
    assert record.created_at is not None
    assert record.updated_at is None


def test_get_task_definition_returns_none_when_missing(db):
    assert db.get_task_definition(999) is None


def test_get_task_definition_by_sha256(db):
    task_id = make_task_definition(db, sha256="deadbeef")

    record = db.get_task_definition_by_sha256("deadbeef")

    assert record is not None
    assert record.id == task_id
    assert record.sha256 == "deadbeef"
    assert db.get_task_definition_by_sha256("missing") is None


def test_list_task_definitions(db):
    make_task_definition(db, name="task-a", sha256="sha-a")
    make_task_definition(db, name="task-b", sha256="sha-b")

    records = db.list_task_definitions()

    assert len(records) == 2
    assert {record.name for record in records} == {"task-a", "task-b"}


def test_update_task_definition(db):
    task_id = make_task_definition(db, sha256="old-sha")

    updated = db.update_task_definition(
        task_id,
        name="new-name",
        desc="new desc",
        sha256="new-sha",
        zip_path="/tmp/new-sha.zip",
        spec='{"name": "new-name"}',
    )

    assert updated is True
    record = db.get_task_definition(task_id)
    assert record.name == "new-name"
    assert record.desc == "new desc"
    assert record.sha256 == "new-sha"
    assert record.zip_path == "/tmp/new-sha.zip"
    assert record.spec == '{"name": "new-name"}'
    assert record.updated_at is not None


def test_update_task_definition_returns_false_when_missing(db):
    updated = db.update_task_definition(
        999, name="x", desc=None, sha256="s", zip_path="p", spec="{}"
    )
    assert updated is False


def test_delete_task_definition_returns_deleted_record(db):
    task_id = make_task_definition(db, name="to-delete", sha256="sha-delete")

    deleted = db.delete_task_definition(task_id)

    assert deleted is not None
    assert deleted.id == task_id
    assert deleted.name == "to-delete"
    assert deleted.sha256 == "sha-delete"
    assert db.get_task_definition(task_id) is None
    assert db.delete_task_definition(task_id) is None


def test_create_job(db):
    """Test creating a new job"""
    job_id = make_job(db)

    assert job_id == 1


def test_get_job(db):
    """Test retrieving a job by ID"""
    task_id = make_task_definition(db, name="rapx", sha256="sha-rapx")
    job_id = db.create_job(
        crate_name="serde",
        version="1.0.193",
        task_id=task_id,
        task_name="rapx",
        task_sha256="sha-rapx",
        task_spec='{"name": "rapx"}',
    )

    job = db.get_job(job_id)

    assert job is not None
    assert isinstance(job, JobRecord)
    assert job.id == job_id
    assert job.crate_name == "serde"
    assert job.version == "1.0.193"
    assert job.task_id == task_id
    assert job.task_name == "rapx"
    assert job.task_sha256 == "sha-rapx"
    assert job.task_spec == '{"name": "rapx"}'
    assert job.status == TaskStatus.PENDING
    assert job.created_at is not None
    assert db.get_job(999) is None


def test_get_all_jobs(db):
    """Test retrieving all jobs"""
    make_job(db, "serde", "1.0.0")
    make_job(db, "tokio", "1.35.0")

    jobs = db.get_all_jobs()

    assert len(jobs) == 2
    assert jobs[0].crate_name == "tokio"  # Latest first
    assert jobs[1].crate_name == "serde"


def test_update_job_status(db):
    """Test updating job status"""
    job_id = make_job(db)

    db.update_job_status(job_id, TaskStatus.RUNNING, started_at=datetime.now())
    job = db.get_job(job_id)

    assert job.status == TaskStatus.RUNNING
    assert job.started_at is not None


def test_get_jobs_by_status(db):
    """Test filtering jobs by status"""
    id1 = make_job(db, "serde", "1.0.0")
    id2 = make_job(db, "tokio", "1.0.0")

    db.update_job_status(id1, TaskStatus.RUNNING)

    running_jobs = db.get_jobs_by_status(TaskStatus.RUNNING)
    pending_jobs = db.get_jobs_by_status(TaskStatus.PENDING)

    assert len(running_jobs) == 1
    assert running_jobs[0].id == id1
    assert len(pending_jobs) == 1
    assert pending_jobs[0].id == id2


def test_get_pending_jobs_ordered_by_priority(db):
    """Pending jobs come back priority-first, then oldest first"""
    low = make_job(db, "low", "1.0.0")
    high = make_job(db, "high", "1.0.0")
    db.update_job_priority(high, 10)

    pending = db.get_pending_jobs_ordered()

    assert [job.id for job in pending] == [high, low]


def test_update_job_pid(db):
    """Test updating job PID"""
    job_id = make_job(db)

    db.update_job_pid(job_id, pid=12345)
    job = db.get_job(job_id)

    assert job.pid == 12345


def test_delete_job(db):
    """Test deleting a job"""
    job_id = make_job(db)

    assert db.delete_job(job_id) is True
    assert db.get_job(job_id) is None
    assert db.delete_job(job_id) is False


def test_reset_job_for_retry(db):
    """Test resetting job state for retry"""
    job_id = make_job(db)

    # Simulate job execution
    db.update_job_status(job_id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_job_pid(job_id, 12345)
    db.update_job_status(
        job_id, TaskStatus.COMPLETED, finished_at=datetime.now(), exit_code=0
    )

    # Verify job is completed
    job = db.get_job(job_id)
    assert job.status == TaskStatus.COMPLETED
    assert job.exit_code == 0
    assert job.pid == 12345
    assert job.started_at is not None
    assert job.finished_at is not None

    # Reset job for retry
    db.reset_job_for_retry(job_id)

    # Verify job is reset
    job = db.get_job(job_id)
    assert job.status == TaskStatus.PENDING
    assert job.exit_code is None
    assert job.pid is None
    assert job.started_at is None
    assert job.finished_at is None
    assert job.error_message is None
    assert job.runner_id is None
    assert job.lease_token is None
    assert job.last_sync_seq == 0

    # Verify crate info and task snapshot are preserved
    assert job.crate_name == "serde"
    assert job.version == "1.0.0"
    assert job.task_name == "cargo-rapx"
    assert job.task_sha256 == "sha-cargo-rapx"


def test_reset_job_for_retry_resets_file_chunk_sequence(db):
    """Retry reset should allow ingesting chunk_seq from 1 again."""
    job_id = make_job(db)

    assert db.record_job_file_chunk(job_id, "stdout.log", 1) is True
    assert db.record_job_file_chunk(job_id, "stdout.log", 1) is False

    db.reset_job_for_retry(job_id)

    assert db.record_job_file_chunk(job_id, "stdout.log", 1) is True


def test_record_job_file_chunk_dedupes_by_job_and_path(db):
    """Chunk dedupe is keyed by (job_id, path)."""
    job1 = make_job(db, "crate-a", "1.0.0")
    job2 = make_job(db, "crate-b", "1.0.0")

    assert db.record_job_file_chunk(job1, "stdout.log", 1) is True
    # Duplicate seq for the same (job, path) is rejected.
    assert db.record_job_file_chunk(job1, "stdout.log", 1) is False
    # Same seq on a different path is accepted.
    assert db.record_job_file_chunk(job1, "stderr.log", 1) is True
    # Same path on a different job is accepted.
    assert db.record_job_file_chunk(job2, "stdout.log", 1) is True
    # Higher seq accepted; replayed seq rejected.
    assert db.record_job_file_chunk(job1, "stdout.log", 2) is True
    assert db.record_job_file_chunk(job1, "stdout.log", 2) is False


def test_reset_job_file_chunk_sequences(db):
    job_id = make_job(db)

    db.record_job_file_chunk(job_id, "stdout.log", 3)
    db.reset_job_file_chunk_sequences(job_id)

    assert db.record_job_file_chunk(job_id, "stdout.log", 1) is True


def test_claim_pending_job_sets_lease_and_running(db):
    job_id = make_job(db)

    job = db.claim_pending_job("runner-1", lease_ttl_seconds=60)

    assert job is not None
    assert job.id == job_id
    assert job.status == TaskStatus.RUNNING
    assert job.runner_id == "runner-1"
    assert job.lease_token
    assert job.lease_expires_at is not None
    assert job.started_at is not None

    # Nothing left to claim.
    assert db.claim_pending_job("runner-1", lease_ttl_seconds=60) is None


def test_claim_pending_job_refreshes_snapshot_from_task_definition(db):
    """A pending job picks up the current task definition when claimed."""
    task_id = make_task_definition(db, name="rapx-v1", sha256="sha-v1")
    job_id = db.create_job(
        crate_name="serde",
        version="1.0.0",
        task_id=task_id,
        task_name="rapx-v1",
        task_sha256="sha-v1",
        task_spec='{"v": 1}',
    )
    assert (
        db.update_task_definition(
            task_id,
            name="rapx-v2",
            desc="updated",
            sha256="sha-v2",
            zip_path="/tmp/sha-v2.zip",
            spec='{"v": 2}',
        )
        is True
    )

    job = db.claim_pending_job("runner-1", lease_ttl_seconds=60)

    assert job is not None
    assert job.task_sha256 == "sha-v2"
    assert job.task_name == "rapx-v2"
    assert job.task_spec == '{"v": 2}'

    # The refreshed snapshot is persisted, not just returned.
    stored = db.get_job(job_id)
    assert stored.task_sha256 == "sha-v2"
    assert stored.task_name == "rapx-v2"


def test_claim_pending_job_keeps_snapshot_when_task_definition_deleted(db):
    """A job whose task definition is gone is claimed with its old snapshot."""
    task_id = make_task_definition(db, name="gone", sha256="sha-gone")
    job_id = db.create_job(
        crate_name="serde",
        version="1.0.0",
        task_id=task_id,
        task_name="gone",
        task_sha256="sha-gone",
        task_spec='{"v": 1}',
    )
    db.delete_task_definition(task_id)

    job = db.claim_pending_job("runner-1", lease_ttl_seconds=60)

    assert job is not None
    assert job.id == job_id
    assert job.task_id == task_id
    assert job.task_name == "gone"
    assert job.task_sha256 == "sha-gone"
    assert job.task_spec == '{"v": 1}'


def test_update_job_state_sync_only_for_running(db):
    job_id = make_job(db)

    assert db.update_job_state_sync(job_id) is False

    db.update_job_status(job_id, TaskStatus.RUNNING)
    assert db.update_job_state_sync(job_id) is True
    assert db.get_job(job_id).last_state_sync_at is not None


def test_reconcile_stale_jobs_marks_overdue_running_as_runner_failed(db):
    job_id = make_job(db)
    db.update_job_status(job_id, TaskStatus.RUNNING, started_at=datetime.now())
    db.update_job_state_sync(job_id)
    db.conn.execute(
        "UPDATE jobs SET last_state_sync_at = ? WHERE id = ?",
        (datetime.now() - timedelta(seconds=3600), job_id),
    )
    db.conn.commit()

    assert db.reconcile_stale_jobs(300) == 1

    job = db.get_job(job_id)
    assert job.status == TaskStatus.RUNNER_FAILED
    assert job.finished_at is not None
    assert job.last_state_sync_at is None

    # Nothing stale left.
    assert db.reconcile_stale_jobs(300) == 0


def test_extend_runner_job_leases(db):
    job_id = make_job(db)
    db.update_job_status(job_id, TaskStatus.RUNNING)
    db.conn.execute("UPDATE jobs SET runner_id = ? WHERE id = ?", ("r1", job_id))
    db.conn.commit()

    assert db.extend_runner_job_leases("r1", 60) == 1
    assert db.get_job(job_id).lease_expires_at is not None
    assert db.extend_runner_job_leases("other-runner", 60) == 0


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


def test_jobs_table_has_distributed_columns(db):
    """Test jobs table has required distributed scheduling columns"""
    cursor = db.conn.cursor()
    columns = {
        row["name"] for row in cursor.execute("PRAGMA table_info(jobs)").fetchall()
    }

    assert "runner_id" in columns
    assert "lease_token" in columns
    assert "lease_expires_at" in columns
    assert "attempt" in columns
    assert "last_sync_seq" in columns
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


def test_apply_job_sync_running_sets_running(db):
    job_id = make_job(db)
    result = db.apply_job_sync(job_id, 1, "running")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.RUNNING
    assert job.started_at is not None
    assert job.finished_at is None


def test_apply_job_sync_running_idempotent(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "running")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.RUNNING
    assert job.finished_at is None


def test_apply_job_sync_completed_sets_terminal(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "completed")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.COMPLETED
    assert job.finished_at is not None


def test_apply_job_sync_failed_sets_terminal(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "failed")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.FAILED
    assert job.finished_at is not None


def test_apply_job_sync_rejects_stale_seq(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    db.apply_job_sync(job_id, 2, "completed")
    result = db.apply_job_sync(job_id, 1, "running")
    assert result is False
    result = db.apply_job_sync(job_id, 2, "failed")
    assert result is False


def test_apply_job_sync_returns_none_for_missing_job(db):
    assert db.apply_job_sync(999, 1, "running") is None


def test_apply_job_sync_timeout_sets_timeout_status(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "timeout")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.TIMEOUT
    assert job.finished_at is not None


def test_apply_job_sync_oom_sets_oom_status(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "oom")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.OOM
    assert job.finished_at is not None


def test_apply_job_sync_cancelled_sets_cancelled_status(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "cancelled")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.CANCELLED
    assert job.finished_at is not None


def test_apply_job_sync_prevents_terminal_to_running_rollback(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    db.apply_job_sync(job_id, 2, "completed")
    result = db.apply_job_sync(job_id, 3, "running")
    assert result is False
    job = db.get_job(job_id)
    assert job.status == TaskStatus.COMPLETED


def test_apply_job_sync_allows_terminal_to_terminal_transition(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    db.apply_job_sync(job_id, 2, "failed")
    result = db.apply_job_sync(job_id, 3, "completed")
    assert result is True
    job = db.get_job(job_id)
    assert job.status == TaskStatus.COMPLETED


def test_apply_job_sync_updates_exit_code_and_message(db):
    job_id = make_job(db)
    db.apply_job_sync(job_id, 1, "running")
    result = db.apply_job_sync(job_id, 2, "failed", exit_code=1, message="build error")
    assert result is True
    job = db.get_job(job_id)
    assert job.exit_code == 1
    assert job.message == "build error"
