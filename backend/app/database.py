"""Database layer for experiment tracking"""

import io
import json
import logging
import re
import secrets
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from core.models import TaskStatus
from core.task_spec import parse_task_spec, sha256_hex, spec_to_dict

logger = logging.getLogger(__name__)


# Placeholder task definition backfilled onto jobs migrated from the legacy
# single-task schema. Users are expected to fill in the real content later via
# the task update API; historical jobs are never re-executed against it.
DUMMY_TASK_TOML = (
    'name = "legacy"\n'
    'desc = "遗留数据占位 task，内容待补充"\n'
    'image = "none"\n'
    'cmd = "true"\n'
    'workspace = "/workspace"\n'
)

# Legacy log files lived at workspace/logs/{task_id}-{log_type}.log
_LEGACY_LOG_FILE_RE = re.compile(r"^(\d+)-(.+)\.log$")

_TASKS_TABLE_DDL = """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        "desc" TEXT,
        sha256 TEXT NOT NULL UNIQUE,
        zip_path TEXT NOT NULL,
        spec TEXT NOT NULL,
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP
    )
"""

_JOBS_TABLE_COLUMNS = """
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        crate_name TEXT NOT NULL,
        version TEXT NOT NULL,
        task_id INTEGER NOT NULL REFERENCES tasks(id),
        task_name TEXT NOT NULL,
        task_sha256 TEXT NOT NULL,
        task_spec TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        started_at TIMESTAMP,
        finished_at TIMESTAMP,
        pid INTEGER,
        exit_code INTEGER,
        error_message TEXT,
        message TEXT,
        memory_used_mb REAL,
        priority INTEGER DEFAULT 0,
        runner_id TEXT,
        lease_token TEXT,
        lease_expires_at TIMESTAMP,
        attempt INTEGER DEFAULT 0,
        last_sync_seq INTEGER DEFAULT 0,
        cancel_requested INTEGER NOT NULL DEFAULT 0,
        last_state_sync_at TIMESTAMP
"""

# Columns carried over when rebuilding a legacy jobs table. The SQL literal is
# substituted when the legacy table predates the column.
_JOBS_KEPT_COLUMNS = [
    ("id", None),
    ("crate_name", "''"),
    ("version", "''"),
    ("status", "'pending'"),
    ("created_at", "CURRENT_TIMESTAMP"),
    ("started_at", "NULL"),
    ("finished_at", "NULL"),
    ("pid", "NULL"),
    ("exit_code", "NULL"),
    ("error_message", "NULL"),
    ("message", "NULL"),
    ("memory_used_mb", "NULL"),
    ("priority", "0"),
    ("runner_id", "NULL"),
    ("lease_token", "NULL"),
    ("lease_expires_at", "NULL"),
    ("attempt", "0"),
    ("last_sync_seq", "0"),
    ("cancel_requested", "0"),
    ("last_state_sync_at", "NULL"),
]


@dataclass
class TaskDefRecord:
    """Data model for a task definition record"""

    id: int
    name: str
    desc: Optional[str]
    sha256: str
    zip_path: str
    spec: str
    created_at: datetime
    updated_at: Optional[datetime] = None


@dataclass
class JobRecord:
    """Data model for a job record (one execution of a task on a crate)"""

    id: int
    crate_name: str
    version: str
    task_id: int
    task_name: str
    task_sha256: str
    task_spec: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
    memory_used_mb: Optional[float] = None
    priority: Optional[int] = None
    runner_id: Optional[str] = None
    lease_token: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    attempt: Optional[int] = None
    last_sync_seq: Optional[int] = None
    cancel_requested: Optional[bool] = None
    last_state_sync_at: Optional[datetime] = None


@dataclass
class RunnerRecord:
    """Data model for a runner record"""

    id: int
    runner_id: str
    token_hash: str
    token_salt: str
    enabled: bool
    created_at: datetime
    last_seen_at: Optional[datetime] = None


def _build_dummy_task_zip() -> bytes:
    """Build the deterministic placeholder task zip in memory."""
    buffer = io.BytesIO()
    entry = zipfile.ZipInfo("task.toml", date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(entry, DUMMY_TASK_TOML)
    return buffer.getvalue()


class Database:
    """SQLite database for tracking experiments"""

    def __init__(self, db_path: str):
        """Initialize database connection

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def init_db(self, workspace: Optional[Path] = None):
        """Initialize database schema, migrating a legacy layout when found.

        Args:
            workspace: Workspace root. Only used by legacy migration for
                filesystem side effects: writing the dummy task zip under
                ``tasks/`` and moving ``logs/{id}-{type}.log`` files to
                ``jobs/{id}/files/``.
        """
        # Create parent directory if needed
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        # Connect and create schema
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()
        cursor.execute("BEGIN")
        try:
            # Legacy layout: the old `tasks` table holds job records.
            if self._is_legacy_schema(cursor):
                cursor.execute("ALTER TABLE tasks RENAME TO jobs")
                logger.info("renamed legacy tasks table to jobs")

            cursor.execute(_TASKS_TABLE_DDL)

            if not self._table_exists(cursor, "jobs"):
                cursor.execute(f"CREATE TABLE jobs ({_JOBS_TABLE_COLUMNS})")
            elif "task_id" not in self._table_columns(cursor, "jobs"):
                self._rebuild_legacy_jobs(cursor, workspace)
            self._create_jobs_indexes(cursor)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runners (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    runner_id TEXT NOT NULL UNIQUE,
                    token_hash TEXT NOT NULL,
                    token_salt TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_seen_at TIMESTAMP
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS job_file_chunk_sequences (
                    job_id INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    last_chunk_seq INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (job_id, path)
                )
            """)
            if self._table_exists(cursor, "task_log_chunk_sequences"):
                cursor.execute("""
                    INSERT OR IGNORE INTO job_file_chunk_sequences
                        (job_id, path, last_chunk_seq)
                    SELECT task_id, log_type, last_chunk_seq
                    FROM task_log_chunk_sequences
                """)
                cursor.execute("DROP TABLE task_log_chunk_sequences")
                logger.info(
                    "migrated task_log_chunk_sequences to job_file_chunk_sequences"
                )

            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

        if workspace is not None:
            self._migrate_legacy_log_files(Path(workspace))

    # ------------------------------------------------------------------
    # Migration helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _table_exists(cursor: sqlite3.Cursor, name: str) -> bool:
        row = cursor.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _table_columns(cursor: sqlite3.Cursor, table: str) -> set:
        return {row["name"] for row in cursor.execute(f"PRAGMA table_info({table})")}

    def _is_legacy_schema(self, cursor: sqlite3.Cursor) -> bool:
        """The legacy layout has job rows in a `tasks` table keyed by crate."""
        return self._table_exists(cursor, "tasks") and "crate_name" in (
            self._table_columns(cursor, "tasks")
        )

    def _create_jobs_indexes(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_pending_priority
            ON jobs(status, priority DESC, created_at ASC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON jobs(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON jobs(status)
        """)

    def _rebuild_legacy_jobs(
        self, cursor: sqlite3.Cursor, workspace: Optional[Path]
    ) -> None:
        """Rebuild a legacy-schema jobs table with task snapshot columns.

        Drops case_count/poc_count/compile_failed/workspace_path/stdout_log/
        stderr_log, adds the task snapshot columns, and backfills every row
        with the dummy task definition.
        """
        row = cursor.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()
        if row["n"] > 0:
            task_id, task_name, task_sha256, task_spec = self._ensure_dummy_task(
                cursor, workspace
            )
        else:
            # Empty legacy table: nothing is backfilled, no dummy is created.
            task_id, task_name, task_sha256, task_spec = 0, "", "", ""

        existing = self._table_columns(cursor, "jobs")
        column_names = []
        select_exprs = []
        for column, default_sql in _JOBS_KEPT_COLUMNS:
            column_names.append(f'"{column}"')
            if column in existing:
                select_exprs.append(f'"{column}"')
            elif column == "last_sync_seq" and "last_event_seq" in existing:
                # Very old databases used last_event_seq before the rename.
                select_exprs.append('"last_event_seq"')
            elif default_sql is not None:
                select_exprs.append(f'{default_sql} AS "{column}"')
            else:
                raise RuntimeError(
                    f"legacy jobs table is missing required column: {column}"
                )

        cursor.execute("DROP TABLE IF EXISTS jobs_new")
        cursor.execute(f"CREATE TABLE jobs_new ({_JOBS_TABLE_COLUMNS})")
        cursor.execute(
            f"""
            INSERT INTO jobs_new
                ({", ".join(column_names)}, task_id, task_name, task_sha256, task_spec)
            SELECT {", ".join(select_exprs)}, ?, ?, ?, ?
            FROM jobs
            """,
            (task_id, task_name, task_sha256, task_spec),
        )
        cursor.execute("DROP TABLE jobs")
        cursor.execute("ALTER TABLE jobs_new RENAME TO jobs")
        logger.info(
            "rebuilt jobs table with task snapshot columns",
            extra={"migrated_rows": row["n"]},
        )

    def _ensure_dummy_task(
        self, cursor: sqlite3.Cursor, workspace: Optional[Path]
    ) -> tuple:
        """Insert (or reuse) the dummy task definition for legacy jobs.

        Returns (id, name, sha256, spec_json). Runs inside the caller's
        transaction.
        """
        zip_bytes = _build_dummy_task_zip()
        sha256 = sha256_hex(zip_bytes)

        row = cursor.execute(
            "SELECT id, name, sha256, spec FROM tasks WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if row is not None:
            return row["id"], row["name"], row["sha256"], row["spec"]

        spec = parse_task_spec(DUMMY_TASK_TOML.encode("utf-8"))
        spec_json = json.dumps(spec_to_dict(spec), ensure_ascii=False)

        if workspace is not None:
            zip_path = Path(workspace) / "tasks" / f"{sha256}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            if not zip_path.exists():
                zip_path.write_bytes(zip_bytes)
            zip_path_str = str(zip_path)
        else:
            zip_path_str = str(Path("tasks") / f"{sha256}.zip")

        cursor.execute(
            """
            INSERT INTO tasks (name, "desc", sha256, zip_path, spec, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (spec.name, spec.desc, sha256, zip_path_str, spec_json, datetime.now()),
        )
        logger.info(
            "created dummy task definition for legacy jobs",
            extra={"task_id": cursor.lastrowid, "sha256": sha256},
        )
        return cursor.lastrowid, spec.name, sha256, spec_json

    def _migrate_legacy_log_files(self, workspace: Path) -> None:
        """Move logs/{id}-{type}.log files to jobs/{id}/files/{type}.log.

        Idempotent: files whose target already exists are skipped (and left in
        place) so re-running never loses data.
        """
        logs_dir = workspace / "logs"
        if not logs_dir.is_dir():
            return
        for entry in sorted(logs_dir.iterdir()):
            if not entry.is_file():
                continue
            match = _LEGACY_LOG_FILE_RE.match(entry.name)
            if match is None:
                continue
            job_id, log_type = match.group(1), match.group(2)
            target_dir = workspace / "jobs" / job_id / "files"
            target = target_dir / f"{log_type}.log"
            if target.exists():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(entry), str(target))
            logger.info(
                "migrated legacy log file",
                extra={"job_id": job_id, "path": f"{log_type}.log"},
            )

    # ------------------------------------------------------------------
    # Task definitions
    # ------------------------------------------------------------------

    def create_task_definition(
        self,
        name: str,
        desc: Optional[str],
        sha256: str,
        zip_path: str,
        spec: str,
    ) -> int:
        """Create a new task definition

        Returns:
            Task ID of the created task definition
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (name, "desc", sha256, zip_path, spec, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (name, desc, sha256, zip_path, spec, datetime.now()),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_task_definition(self, task_id: int) -> Optional[TaskDefRecord]:
        """Get a task definition by ID"""
        cursor = self.conn.cursor()
        row = cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_task_def_record(row)

    def get_task_definition_by_sha256(self, sha256: str) -> Optional[TaskDefRecord]:
        """Get a task definition by its zip sha256 fingerprint"""
        cursor = self.conn.cursor()
        row = cursor.execute(
            "SELECT * FROM tasks WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_task_def_record(row)

    def list_task_definitions(self) -> List[TaskDefRecord]:
        """List all task definitions, newest first"""
        cursor = self.conn.cursor()
        rows = cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
        return [self._row_to_task_def_record(row) for row in rows]

    def update_task_definition(
        self,
        task_id: int,
        *,
        name: str,
        desc: Optional[str],
        sha256: str,
        zip_path: str,
        spec: str,
    ) -> bool:
        """Replace a task definition; returns True when the row exists."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tasks
            SET name = ?, "desc" = ?, sha256 = ?, zip_path = ?, spec = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (name, desc, sha256, zip_path, spec, datetime.now(), task_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def delete_task_definition(self, task_id: int) -> Optional[TaskDefRecord]:
        """Delete a task definition; returns the deleted record if it existed."""
        record = self.get_task_definition(task_id)
        if record is None:
            return None
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return record

    def _row_to_task_def_record(self, row: sqlite3.Row) -> TaskDefRecord:
        """Convert a database row to a TaskDefRecord"""
        return TaskDefRecord(
            id=row["id"],
            name=row["name"],
            desc=row["desc"],
            sha256=row["sha256"],
            zip_path=row["zip_path"],
            spec=row["spec"],
            created_at=self._parse_datetime(row["created_at"]),
            updated_at=(
                self._parse_datetime(row["updated_at"]) if row["updated_at"] else None
            ),
        )

    # ------------------------------------------------------------------
    # Jobs
    # ------------------------------------------------------------------

    def create_job(
        self,
        crate_name: str,
        version: str,
        task_id: int,
        task_name: str,
        task_sha256: str,
        task_spec: str,
    ) -> int:
        """Create a new job

        Args:
            crate_name: Name of the crate
            version: Version of the crate
            task_id: Task definition this job runs
            task_name: Snapshot of the task name
            task_sha256: Snapshot of the task zip sha256
            task_spec: Snapshot of the parsed task spec (JSON string)

        Returns:
            Job ID of the created job
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO jobs (
                crate_name, version, task_id, task_name, task_sha256, task_spec,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                crate_name,
                version,
                task_id,
                task_name,
                task_sha256,
                task_spec,
                TaskStatus.PENDING.value,
                datetime.now(),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_job(self, job_id: int) -> Optional[JobRecord]:
        """Get a job by ID

        Args:
            job_id: Job ID to retrieve

        Returns:
            JobRecord if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_job_record(row)

    def get_all_jobs(self) -> List[JobRecord]:
        """Get all jobs ordered by creation time (latest first)

        Returns:
            List of JobRecord objects
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM jobs ORDER BY created_at DESC")
        rows = cursor.fetchall()

        return [self._row_to_job_record(row) for row in rows]

    _JOB_SORT_SQL = {
        "id": "id",
        "crate_name": "crate_name",
        "version": "version",
        "task_name": "task_name",
        "status": "status",
        "started_at": "started_at",
        "runner_id": "runner_id",
        "created_at": "created_at",
        "runtime": "(julianday(COALESCE(finished_at, 'now')) - julianday(started_at))",
    }

    def query_jobs(
        self,
        *,
        status: Optional[str] = None,
        crate_name: Optional[str] = None,
        task_name: Optional[str] = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, List[JobRecord]]:
        """Query jobs with compound filters, sorting and pagination.

        ``crate_name`` and ``task_name`` match case-insensitively as
        substrings. Returns ``(total_matching, page_items)``.
        """
        where: List[str] = []
        params: List[object] = []

        if status:
            where.append("status = ?")
            params.append(status)
        if crate_name:
            where.append("crate_name LIKE ? ESCAPE '\\'")
            params.append(f"%{self._escape_like(crate_name)}%")
        if task_name:
            where.append("task_name LIKE ? ESCAPE '\\'")
            params.append(f"%{self._escape_like(task_name)}%")

        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        cursor = self.conn.cursor()

        cursor.execute(f"SELECT COUNT(*) FROM jobs{where_sql}", params)
        total = cursor.fetchone()[0]

        column = self._JOB_SORT_SQL.get(sort_by, "created_at")
        direction = "ASC" if sort_dir == "asc" else "DESC"
        cursor.execute(
            f"""
            SELECT * FROM jobs{where_sql}
            ORDER BY ({column}) IS NULL, {column} {direction}, id DESC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        )
        rows = cursor.fetchall()
        return total, [self._row_to_job_record(row) for row in rows]

    @staticmethod
    def _escape_like(value: str) -> str:
        """Escape LIKE wildcards so user input matches literally."""
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    def get_jobs_by_status(self, status: TaskStatus) -> List[JobRecord]:
        """Get jobs filtered by status

        Args:
            status: Status to filter by

        Returns:
            List of JobRecord objects with matching status
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC",
            (status.value,),
        )
        rows = cursor.fetchall()

        return [self._row_to_job_record(row) for row in rows]

    def get_pending_jobs_ordered(self) -> List[JobRecord]:
        """Get pending jobs ordered by priority (high first), then creation time."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY priority DESC, created_at ASC",
            (TaskStatus.PENDING.value,),
        )
        rows = cursor.fetchall()
        return [self._row_to_job_record(row) for row in rows]

    def delete_job(self, job_id: int) -> bool:
        """Delete a job from database

        Args:
            job_id: Job ID to delete

        Returns:
            True if job was deleted, False if job not found
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def update_job_status(
        self,
        job_id: int,
        status: TaskStatus,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        message: Optional[str] = None,
    ):
        """Update job status and timestamps

        Args:
            job_id: Job ID to update
            status: New job status
            started_at: Job start time (optional)
            finished_at: Job completion time (optional)
            exit_code: Exit code (optional)
            error_message: Error message (optional)
        """
        cursor = self.conn.cursor()

        updates = ["status = ?"]
        params = [status.value]

        if started_at is not None:
            updates.append("started_at = ?")
            params.append(started_at)

        if finished_at is not None:
            updates.append("finished_at = ?")
            params.append(finished_at)

        if exit_code is not None:
            updates.append("exit_code = ?")
            params.append(exit_code)

        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)

        if message is not None:
            updates.append("message = ?")
            params.append(message)

        params.append(job_id)

        query = f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self.conn.commit()

    def update_job_priority(self, job_id: int, priority: int):
        """Update job priority."""
        cursor = self.conn.cursor()
        cursor.execute("UPDATE jobs SET priority = ? WHERE id = ?", (priority, job_id))
        self.conn.commit()

    def update_job_pid(self, job_id: int, pid: int):
        """Update job process ID

        Args:
            job_id: Job ID to update
            pid: Process ID
        """
        cursor = self.conn.cursor()
        cursor.execute("UPDATE jobs SET pid = ? WHERE id = ?", (pid, job_id))
        self.conn.commit()

    def reset_job_for_retry(self, job_id: int):
        """Reset job state for retry

        Resets job to pending status and clears execution results.
        Keeps crate info and the task snapshot intact.

        Args:
            job_id: Job ID to reset
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE jobs SET
                status = ?,
                started_at = NULL,
                finished_at = NULL,
                exit_code = NULL,
                error_message = NULL,
                message = NULL,
                pid = NULL,
                runner_id = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                last_sync_seq = 0,
                cancel_requested = 0,
                last_state_sync_at = NULL
            WHERE id = ?
        """,
            (TaskStatus.PENDING.value, job_id),
        )
        cursor.execute(
            "DELETE FROM job_file_chunk_sequences WHERE job_id = ?",
            (job_id,),
        )
        self.conn.commit()

    def update_job_state_sync(self, job_id: int) -> bool:
        """Update the last_state_sync_at timestamp for a running job.

        Args:
            job_id: Job ID to update

        Returns:
            True if a row was updated, False otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE jobs SET last_state_sync_at = ? WHERE id = ? AND status = ?",
            (datetime.now(), job_id, TaskStatus.RUNNING.value),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    def reconcile_stale_jobs(self, max_sync_interval_seconds: int) -> int:
        """Mark RUNNING jobs that haven't synced within the threshold as runner_failed.

        Args:
            max_sync_interval_seconds: Maximum allowed interval since last sync

        Returns:
            Number of jobs marked as runner_failed
        """
        now = datetime.now()
        threshold = now - timedelta(seconds=max_sync_interval_seconds)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE jobs
            SET status = ?,
                finished_at = ?,
                last_state_sync_at = NULL,
                error_message = COALESCE(error_message, ?)
            WHERE status = ?
              AND last_state_sync_at IS NOT NULL
              AND last_state_sync_at <= ?
            """,
            (
                TaskStatus.RUNNER_FAILED.value,
                now,
                "Runner stopped sending state sync updates",
                TaskStatus.RUNNING.value,
                threshold,
            ),
        )
        self.conn.commit()
        return cursor.rowcount

    def apply_job_sync(
        self,
        job_id: int,
        sync_seq: int,
        status: str,
        exit_code: Optional[int] = None,
        message: Optional[str] = None,
    ) -> Optional[bool]:
        """Apply an ordered runner sync.

        Returns:
            None: job does not exist
            False: duplicate/stale sequence or invalid state transition
            True: sync applied
        """
        cursor = self.conn.cursor()
        now = datetime.now()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT status, last_sync_seq FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()

            if row is None:
                self.conn.commit()
                return None

            last_sync_seq = row["last_sync_seq"] or 0
            if sync_seq <= last_sync_seq:
                self.conn.commit()
                return False

            current_status = TaskStatus(row["status"])
            new_status = TaskStatus(status)
            terminal_statuses = {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
                TaskStatus.TIMEOUT,
                TaskStatus.OOM,
                TaskStatus.RUNNER_FAILED,
            }

            # Prevent rollback from terminal to non-terminal
            if current_status in terminal_statuses and new_status == TaskStatus.RUNNING:
                self.conn.commit()
                return False

            updates = ["last_sync_seq = ?", "last_state_sync_at = ?"]
            params = [sync_seq, now]

            updates.append("status = ?")
            params.append(new_status.value)

            if new_status == TaskStatus.RUNNING:
                updates.append("started_at = COALESCE(started_at, ?)")
                params.append(now)
            elif new_status in terminal_statuses:
                updates.append("finished_at = ?")
                params.append(now)

            if exit_code is not None:
                updates.append("exit_code = ?")
                params.append(exit_code)

            if message is not None:
                updates.append("message = ?")
                params.append(message)

            params.append(job_id)
            cursor.execute(
                f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def record_job_file_chunk(
        self,
        job_id: int,
        path: str,
        chunk_seq: int,
    ) -> bool:
        """Record chunk sequence and return whether append should occur."""
        cursor = self.conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                """
                SELECT last_chunk_seq
                FROM job_file_chunk_sequences
                WHERE job_id = ? AND path = ?
                """,
                (job_id, path),
            ).fetchone()

            if row is not None and chunk_seq <= row["last_chunk_seq"]:
                self.conn.commit()
                return False

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO job_file_chunk_sequences (job_id, path, last_chunk_seq)
                    VALUES (?, ?, ?)
                    """,
                    (job_id, path, chunk_seq),
                )
            else:
                cursor.execute(
                    """
                    UPDATE job_file_chunk_sequences
                    SET last_chunk_seq = ?
                    WHERE job_id = ? AND path = ?
                    """,
                    (chunk_seq, job_id, path),
                )

            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def reset_job_file_chunk_sequences(self, job_id: int):
        """Clear per-file chunk sequence tracking for a job."""
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM job_file_chunk_sequences WHERE job_id = ?",
            (job_id,),
        )
        self.conn.commit()

    def extend_runner_job_leases(self, runner_id: str, lease_ttl_seconds: int) -> int:
        """Extend lease expiration of all RUNNING jobs for a runner."""
        now = datetime.now()
        lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE jobs
            SET lease_expires_at = ?
            WHERE status = ? AND runner_id = ?
            """,
            (lease_expires_at, TaskStatus.RUNNING.value, runner_id),
        )
        self.conn.commit()
        return cursor.rowcount

    def claim_pending_job(
        self, runner_id: str, lease_ttl_seconds: int
    ) -> Optional[JobRecord]:
        """Atomically claim one pending job for a runner.

        The claimed job's task snapshot (task_name/task_sha256/task_spec) is
        refreshed from the current task definition when one exists, so updates
        to a task take effect for jobs that have not started yet. Jobs whose
        task definition has been deleted are claimed with their old snapshot.
        """
        now = datetime.now()
        lease_token = secrets.token_urlsafe(24)
        lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        cursor = self.conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                """
                SELECT j.id AS id,
                       j.task_sha256 AS current_sha256,
                       t.id AS def_id,
                       t.name AS def_name,
                       t.sha256 AS def_sha256,
                       t.spec AS def_spec
                FROM jobs j
                LEFT JOIN tasks t ON t.id = j.task_id
                WHERE j.status = ?
                ORDER BY j.priority DESC, j.created_at ASC
                LIMIT 1
                """,
                (TaskStatus.PENDING.value,),
            ).fetchone()

            if row is None:
                self.conn.commit()
                return None

            job_id = row["id"]
            if row["def_id"] is not None and row["def_sha256"] != row["current_sha256"]:
                cursor.execute(
                    """
                    UPDATE jobs
                    SET task_name = ?, task_sha256 = ?, task_spec = ?
                    WHERE id = ?
                    """,
                    (row["def_name"], row["def_sha256"], row["def_spec"], job_id),
                )

            cursor.execute(
                """
                UPDATE jobs
                SET status = ?,
                    runner_id = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    finished_at = NULL,
                    exit_code = NULL,
                    error_message = NULL,
                    message = NULL,
                    pid = NULL,
                    last_sync_seq = 0,
                    cancel_requested = 0,
                    last_state_sync_at = ?
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    runner_id,
                    lease_token,
                    lease_expires_at,
                    now,
                    now,
                    job_id,
                    TaskStatus.PENDING.value,
                ),
            )

            if cursor.rowcount == 0:
                self.conn.commit()
                return None

            self.conn.commit()
            return self.get_job(job_id)
        except Exception:
            self.conn.rollback()
            raise

    def _row_to_job_record(self, row: sqlite3.Row) -> JobRecord:
        """Convert a database row to a JobRecord

        Args:
            row: Database row

        Returns:
            JobRecord object
        """
        return JobRecord(
            id=row["id"],
            crate_name=row["crate_name"],
            version=row["version"],
            task_id=row["task_id"],
            task_name=row["task_name"],
            task_sha256=row["task_sha256"],
            task_spec=row["task_spec"],
            status=TaskStatus(row["status"]),
            created_at=self._parse_datetime(row["created_at"]),
            started_at=(
                self._parse_datetime(row["started_at"]) if row["started_at"] else None
            ),
            finished_at=(
                self._parse_datetime(row["finished_at"]) if row["finished_at"] else None
            ),
            pid=row["pid"],
            exit_code=row["exit_code"],
            error_message=row["error_message"],
            message=row["message"],
            memory_used_mb=row["memory_used_mb"],
            priority=row["priority"],
            runner_id=row["runner_id"],
            lease_token=row["lease_token"],
            lease_expires_at=(
                self._parse_datetime(row["lease_expires_at"])
                if row["lease_expires_at"]
                else None
            ),
            attempt=row["attempt"],
            last_sync_seq=row["last_sync_seq"],
            cancel_requested=bool(row["cancel_requested"]),
            last_state_sync_at=(
                self._parse_datetime(row["last_state_sync_at"])
                if row["last_state_sync_at"]
                else None
            ),
        )

    # ------------------------------------------------------------------
    # Runners
    # ------------------------------------------------------------------

    def _row_to_runner_record(self, row: sqlite3.Row) -> RunnerRecord:
        """Convert a database row to a RunnerRecord."""
        return RunnerRecord(
            id=row["id"],
            runner_id=row["runner_id"],
            token_hash=row["token_hash"],
            token_salt=row["token_salt"],
            enabled=bool(row["enabled"]),
            created_at=self._parse_datetime(row["created_at"]),
            last_seen_at=(
                self._parse_datetime(row["last_seen_at"])
                if row["last_seen_at"]
                else None
            ),
        )

    def create_runner(
        self,
        runner_id: str,
        token_hash: str,
        token_salt: str,
    ) -> RunnerRecord:
        """Create a new runner record."""
        cursor = self.conn.cursor()
        created_at = datetime.now()
        cursor.execute(
            """
            INSERT INTO runners (runner_id, token_hash, token_salt, enabled, created_at)
            VALUES (?, ?, ?, 1, ?)
        """,
            (runner_id, token_hash, token_salt, created_at),
        )
        self.conn.commit()
        return RunnerRecord(
            id=cursor.lastrowid,
            runner_id=runner_id,
            token_hash=token_hash,
            token_salt=token_salt,
            enabled=True,
            created_at=created_at,
            last_seen_at=None,
        )

    def get_runner_by_runner_id(self, runner_id: str) -> Optional[RunnerRecord]:
        """Get runner by stable runner_id."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM runners WHERE runner_id = ?", (runner_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return self._row_to_runner_record(row)

    def list_runners(self) -> List[RunnerRecord]:
        """List all runners ordered by creation time."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM runners ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [self._row_to_runner_record(row) for row in rows]

    def disable_runner(self, runner_id: str) -> bool:
        """Disable a runner; returns True when runner exists."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM runners WHERE runner_id = ?", (runner_id,))
        if cursor.fetchone() is None:
            return False

        cursor.execute(
            "UPDATE runners SET enabled = 0 WHERE runner_id = ?", (runner_id,)
        )
        self.conn.commit()
        return True

    def enable_runner(self, runner_id: str) -> bool:
        """Enable a runner; returns True when runner exists."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM runners WHERE runner_id = ?", (runner_id,))
        if cursor.fetchone() is None:
            return False

        cursor.execute(
            "UPDATE runners SET enabled = 1 WHERE runner_id = ?", (runner_id,)
        )
        self.conn.commit()
        return True

    def delete_runner(self, runner_id: str) -> bool:
        """Delete a runner; returns True when a row was deleted."""
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM runners WHERE runner_id = ?", (runner_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def touch_runner_heartbeat(self, runner_id: str) -> bool:
        """Update runner heartbeat timestamp; returns True when updated."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE runners SET last_seen_at = ? WHERE runner_id = ?",
            (datetime.now(), runner_id),
        )
        self.conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _parse_datetime(self, dt_str: str) -> datetime:
        """Parse datetime from database

        Args:
            dt_str: Datetime string

        Returns:
            datetime object
        """
        if isinstance(dt_str, datetime):
            return dt_str

        # Try different datetime formats
        for fmt in [
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ]:
            try:
                return datetime.strptime(dt_str, fmt)
            except ValueError:
                continue

        # If all formats fail, raise error
        raise ValueError(f"Cannot parse datetime: {dt_str}")

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
