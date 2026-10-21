"""Tests for migration from the legacy single-task schema to the tasks/jobs schema."""

import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from app.database import Database
from core.models import TaskStatus
from core.task_spec import parse_task_spec, sha256_hex, spec_from_dict

LEGACY_SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crate_name TEXT NOT NULL,
    version TEXT NOT NULL,
    workspace_path TEXT NOT NULL,
    stdout_log TEXT NOT NULL,
    stderr_log TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    finished_at TIMESTAMP,
    case_count INTEGER,
    poc_count INTEGER,
    pid INTEGER,
    exit_code INTEGER,
    error_message TEXT,
    message TEXT,
    memory_used_mb REAL,
    compile_failed INTEGER,
    priority INTEGER DEFAULT 0,
    runner_id TEXT,
    lease_token TEXT,
    lease_expires_at TIMESTAMP,
    attempt INTEGER DEFAULT 0,
    last_sync_seq INTEGER DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    last_state_sync_at TIMESTAMP
);
CREATE TABLE runners (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    runner_id TEXT NOT NULL UNIQUE,
    token_hash TEXT NOT NULL,
    token_salt TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP
);
CREATE TABLE task_log_chunk_sequences (
    task_id INTEGER NOT NULL,
    log_type TEXT NOT NULL,
    last_chunk_seq INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (task_id, log_type)
);
"""


def _create_legacy_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.executescript(LEGACY_SCHEMA)
    conn.execute(
        """
        INSERT INTO tasks (
            crate_name, version, workspace_path, stdout_log, stderr_log,
            status, case_count, poc_count, compile_failed, memory_used_mb,
            message, priority, exit_code
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "serde",
            "1.0.0",
            "/tmp/ws/serde-1.0.0",
            "/tmp/logs/1-stdout.log",
            "/tmp/logs/1-stderr.log",
            "completed",
            10,
            3,
            1,
            512.5,
            "done",
            5,
            0,
        ),
    )
    conn.execute(
        """
        INSERT INTO tasks (
            crate_name, version, workspace_path, stdout_log, stderr_log,
            status, error_message
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "tokio",
            "1.35.0",
            "/tmp/ws/tokio-1.35.0",
            "/tmp/logs/2-stdout.log",
            "/tmp/logs/2-stderr.log",
            "failed",
            "boom",
        ),
    )
    conn.execute(
        "INSERT INTO task_log_chunk_sequences (task_id, log_type, last_chunk_seq) "
        "VALUES (1, 'stdout', 5)"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def legacy_workspace(tmp_path):
    """A workspace holding a legacy-schema database and legacy log files."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = workspace / "tasks.db"
    _create_legacy_db(db_path)
    logs = workspace / "logs"
    logs.mkdir()
    (logs / "1-stdout.log").write_text("first stdout\n")
    (logs / "1-stderr.log").write_text("first stderr\n")
    (logs / "2-runner.log").write_text("runner log\n")
    (logs / "2-miri_report.log").write_text("miri report\n")
    (logs / "2-stats-yaml.log").write_text("stats yaml\n")
    return workspace, db_path


def test_legacy_rows_migrated_to_jobs_with_dummy_task(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)

    jobs = db.get_all_jobs()
    assert len(jobs) == 2
    by_crate = {job.crate_name: job for job in jobs}
    serde = by_crate["serde"]
    tokio = by_crate["tokio"]

    definitions = db.list_task_definitions()
    assert len(definitions) == 1
    dummy = definitions[0]
    assert dummy.name == "legacy"

    for job in (serde, tokio):
        assert job.task_id == dummy.id
        assert job.task_name == "legacy"
        assert job.task_sha256 == dummy.sha256
        # task_spec snapshot stays parseable
        spec = spec_from_dict(json.loads(job.task_spec))
        assert spec.name == "legacy"

    # Legacy data is preserved.
    assert serde.id == 1
    assert tokio.id == 2
    assert serde.status == TaskStatus.COMPLETED
    assert serde.version == "1.0.0"
    assert serde.memory_used_mb == 512.5
    assert serde.message == "done"
    assert serde.priority == 5
    assert serde.exit_code == 0
    assert tokio.status == TaskStatus.FAILED
    assert tokio.error_message == "boom"


def test_jobs_table_drops_legacy_columns(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)

    cursor = db.conn.cursor()
    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(jobs)")}

    for dropped in (
        "case_count",
        "poc_count",
        "compile_failed",
        "workspace_path",
        "stdout_log",
        "stderr_log",
    ):
        assert dropped not in columns

    for kept in (
        "id",
        "crate_name",
        "version",
        "task_id",
        "task_name",
        "task_sha256",
        "task_spec",
        "status",
        "created_at",
        "started_at",
        "finished_at",
        "pid",
        "exit_code",
        "error_message",
        "message",
        "memory_used_mb",
        "priority",
        "runner_id",
        "lease_token",
        "lease_expires_at",
        "attempt",
        "last_sync_seq",
        "cancel_requested",
        "last_state_sync_at",
    ):
        assert kept in columns


def test_dummy_zip_written_to_workspace_and_parseable(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)

    dummy = db.list_task_definitions()[0]
    zip_path = Path(dummy.zip_path)
    assert zip_path.exists()
    assert zip_path.parent == workspace / "tasks"

    data = zip_path.read_bytes()
    assert sha256_hex(data) == dummy.sha256

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        toml_bytes = zf.read("task.toml")
    spec = parse_task_spec(toml_bytes)
    assert spec.name == "legacy"
    assert spec.desc == "遗留数据占位 task，内容待补充"
    assert spec.image == "none"
    assert spec.cmd == "true"
    assert spec.workspace == "/workspace"

    stored_spec = json.loads(dummy.spec)
    assert stored_spec["name"] == "legacy"
    assert stored_spec["cmd"] == "true"


def test_init_db_is_idempotent_after_migration(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)
    dummy_sha256 = db.list_task_definitions()[0].sha256
    db.close()

    db2 = Database(str(db_path))
    db2.init_db(workspace)

    definitions = db2.list_task_definitions()
    assert len(definitions) == 1
    assert definitions[0].sha256 == dummy_sha256
    assert len(db2.get_all_jobs()) == 2

    # The rebuilt AUTOINCREMENT table keeps issuing fresh ids.
    new_job_id = db2.create_job(
        crate_name="anyhow",
        version="1.0.0",
        task_id=definitions[0].id,
        task_name=definitions[0].name,
        task_sha256=definitions[0].sha256,
        task_spec=definitions[0].spec,
    )
    assert new_job_id == 3


def test_migration_moves_legacy_log_files(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)

    expected = {
        ("1", "stdout"): "first stdout\n",
        ("1", "stderr"): "first stderr\n",
        ("2", "runner"): "runner log\n",
        ("2", "miri_report"): "miri report\n",
        ("2", "stats-yaml"): "stats yaml\n",
    }
    for (job_id, log_type), content in expected.items():
        target = workspace / "jobs" / job_id / "files" / f"{log_type}.log"
        assert target.read_text() == content
        assert not (workspace / "logs" / f"{job_id}-{log_type}.log").exists()


def test_migration_log_move_skips_existing_target(legacy_workspace):
    workspace, db_path = legacy_workspace
    existing = workspace / "jobs" / "1" / "files" / "stdout.log"
    existing.parent.mkdir(parents=True)
    existing.write_text("already here\n")

    db = Database(str(db_path))
    db.init_db(workspace)

    assert existing.read_text() == "already here\n"
    # Skipped source files stay put rather than being lost.
    assert (workspace / "logs" / "1-stdout.log").read_text() == "first stdout\n"


def test_migration_with_empty_legacy_table_creates_no_dummy(tmp_path):
    """A renamed but empty legacy table is rebuilt without a dummy task."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = workspace / "tasks.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()

    db = Database(str(db_path))
    db.init_db(workspace)

    assert db.list_task_definitions() == []
    assert db.get_all_jobs() == []
    cursor = db.conn.cursor()
    columns = {row["name"] for row in cursor.execute("PRAGMA table_info(jobs)")}
    assert "task_id" in columns
    assert "crate_name" in columns
    assert "case_count" not in columns


def test_migration_from_oldest_legacy_schema(tmp_path):
    """Legacy DBs predating the incremental ALTER migrations still migrate."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db_path = workspace / "tasks.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            crate_name TEXT NOT NULL,
            version TEXT NOT NULL,
            workspace_path TEXT NOT NULL,
            stdout_log TEXT NOT NULL,
            stderr_log TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            finished_at TIMESTAMP,
            case_count INTEGER,
            poc_count INTEGER,
            pid INTEGER,
            exit_code INTEGER,
            error_message TEXT,
            message TEXT,
            memory_used_mb REAL
        )
    """)
    conn.execute(
        """
        INSERT INTO tasks (
            crate_name, version, workspace_path, stdout_log, stderr_log,
            status, memory_used_mb
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("serde", "1.0.0", "/ws", "/l1", "/l2", "completed", 128.0),
    )
    conn.commit()
    conn.close()

    db = Database(str(db_path))
    db.init_db(workspace)

    jobs = db.get_all_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.crate_name == "serde"
    assert job.status == TaskStatus.COMPLETED
    assert job.memory_used_mb == 128.0
    # Columns added by later migrations get their defaults.
    assert job.priority == 0
    assert job.attempt == 0
    assert job.last_sync_seq == 0
    assert job.cancel_requested is False
    assert job.runner_id is None
    assert job.task_name == "legacy"


def test_migration_renames_chunk_sequence_table(legacy_workspace):
    workspace, db_path = legacy_workspace
    db = Database(str(db_path))
    db.init_db(workspace)

    cursor = db.conn.cursor()
    tables = {
        row["name"]
        for row in cursor.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert "task_log_chunk_sequences" not in tables
    assert "job_file_chunk_sequences" in tables

    # Sequence data carried over from (task_id, log_type) to (job_id, path).
    assert db.record_job_file_chunk(1, "stdout", 5) is False
    assert db.record_job_file_chunk(1, "stdout", 6) is True


def test_fresh_deploy_creates_new_schema_without_dummy(tmp_path):
    workspace = tmp_path / "workspace"
    db = Database(str(tmp_path / "tasks.db"))
    db.init_db(workspace)

    assert db.list_task_definitions() == []
    assert db.get_all_jobs() == []

    cursor = db.conn.cursor()
    task_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(tasks)")}
    assert task_columns == {
        "id",
        "name",
        "desc",
        "sha256",
        "zip_path",
        "spec",
        "created_at",
        "updated_at",
    }
    job_columns = {row["name"] for row in cursor.execute("PRAGMA table_info(jobs)")}
    assert "task_id" in job_columns
    assert "case_count" not in job_columns
    chunk_columns = {
        row["name"]
        for row in cursor.execute("PRAGMA table_info(job_file_chunk_sequences)")
    }
    assert chunk_columns == {"job_id", "path", "last_chunk_seq"}
