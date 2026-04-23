"""Database layer for experiment tracking"""

import sqlite3
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, List

from core.models import TaskStatus


@dataclass
class TaskRecord:
    """Data model for a task record"""

    id: int
    crate_name: str
    version: str
    workspace_path: str
    stdout_log: str
    stderr_log: str
    status: TaskStatus
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    case_count: Optional[int] = None
    poc_count: Optional[int] = None
    pid: Optional[int] = None
    exit_code: Optional[int] = None
    error_message: Optional[str] = None
    message: Optional[str] = None
    memory_used_mb: Optional[float] = None
    compile_failed: Optional[int] = None
    priority: Optional[int] = None
    runner_id: Optional[str] = None
    lease_token: Optional[str] = None
    lease_expires_at: Optional[datetime] = None
    attempt: Optional[int] = None
    last_event_seq: Optional[int] = None
    cancel_requested: Optional[bool] = None


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


class Database:
    """SQLite database for tracking experiments"""

    def __init__(self, db_path: str):
        """Initialize database connection

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None

    def init_db(self):
        """Initialize database schema"""
        # Create parent directory if needed
        db_file = Path(self.db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)

        # Connect and create schema
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
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
            CREATE TABLE IF NOT EXISTS task_log_chunk_sequences (
                task_id INTEGER NOT NULL,
                log_type TEXT NOT NULL,
                last_chunk_seq INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (task_id, log_type)
            )
            """)
        # Backward-compatible migration for existing databases.
        columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "message" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN message TEXT")
        if "compile_failed" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN compile_failed INTEGER")
        if "priority" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN priority INTEGER DEFAULT 0")
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_pending_priority
                ON tasks(status, priority DESC, created_at ASC)
            """)
        if "runner_id" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN runner_id TEXT")
        if "lease_token" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN lease_token TEXT")
        if "lease_expires_at" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN lease_expires_at TIMESTAMP")
        if "attempt" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN attempt INTEGER DEFAULT 0")
        if "last_event_seq" not in columns:
            cursor.execute(
                "ALTER TABLE tasks ADD COLUMN last_event_seq INTEGER DEFAULT 0"
            )
        if "cancel_requested" not in columns:
            cursor.execute(
                "ALTER TABLE tasks ADD COLUMN cancel_requested INTEGER NOT NULL DEFAULT 0"
            )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
        """)
        self.conn.commit()

    def apply_task_event(
        self, task_id: int, event_seq: int, event_type: str
    ) -> Optional[bool]:
        """Apply an ordered runner event.

        Returns:
            None: task does not exist
            False: duplicate/stale event sequence (idempotent no-op)
            True: event applied
        """
        cursor = self.conn.cursor()
        now = datetime.now()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                "SELECT last_event_seq FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()

            if row is None:
                self.conn.commit()
                return None

            last_event_seq = row["last_event_seq"] or 0
            if event_seq <= last_event_seq:
                self.conn.commit()
                return False

            updates = ["last_event_seq = ?"]
            params = [event_seq]

            if event_type in ("started", "progress"):
                updates.append("status = ?")
                params.append(TaskStatus.RUNNING.value)
                updates.append("started_at = COALESCE(started_at, ?)")
                params.append(now)
            else:
                # All non-running events are terminal
                if event_type == "completed":
                    terminal_status = TaskStatus.COMPLETED.value
                elif event_type == "cancelled":
                    terminal_status = TaskStatus.CANCELLED.value
                elif event_type == "timeout":
                    terminal_status = TaskStatus.TIMEOUT.value
                elif event_type == "oom":
                    terminal_status = TaskStatus.OOM.value
                else:
                    terminal_status = TaskStatus.FAILED.value
                updates.append("status = ?")
                params.append(terminal_status)
                updates.append("finished_at = ?")
                params.append(now)

            params.append(task_id)
            cursor.execute(
                f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def record_task_log_chunk(
        self,
        task_id: int,
        log_type: str,
        chunk_seq: int,
    ) -> bool:
        """Record chunk sequence and return whether append should occur."""
        cursor = self.conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                """
                SELECT last_chunk_seq
                FROM task_log_chunk_sequences
                WHERE task_id = ? AND log_type = ?
                """,
                (task_id, log_type),
            ).fetchone()

            if row is not None and chunk_seq <= row["last_chunk_seq"]:
                self.conn.commit()
                return False

            if row is None:
                cursor.execute(
                    """
                    INSERT INTO task_log_chunk_sequences (task_id, log_type, last_chunk_seq)
                    VALUES (?, ?, ?)
                    """,
                    (task_id, log_type, chunk_seq),
                )
            else:
                cursor.execute(
                    """
                    UPDATE task_log_chunk_sequences
                    SET last_chunk_seq = ?
                    WHERE task_id = ? AND log_type = ?
                    """,
                    (chunk_seq, task_id, log_type),
                )

            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def create_task(
        self,
        crate_name: str,
        version: str,
        workspace_path: str,
        stdout_log: str,
        stderr_log: str,
    ) -> int:
        """Create a new task

        Args:
            crate_name: Name of the crate
            version: Version of the crate
            workspace_path: Path to workspace directory
            stdout_log: Path to stdout log file
            stderr_log: Path to stderr log file

        Returns:
            Task ID of the created task
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            INSERT INTO tasks (
                crate_name, version, workspace_path,
                stdout_log, stderr_log, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (
                crate_name,
                version,
                workspace_path,
                stdout_log,
                stderr_log,
                TaskStatus.PENDING.value,
                datetime.now(),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        """Get a task by ID

        Args:
            task_id: Task ID to retrieve

        Returns:
            TaskRecord if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_task_record(row)

    def get_task_by_crate_and_version(
        self, crate_name: str, version: str
    ) -> Optional[TaskRecord]:
        """Get a task by crate name and version

        Args:
            crate_name: Name of the crate
            version: Version of the crate

        Returns:
            TaskRecord if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE crate_name = ? AND version = ?",
            (crate_name, version),
        )
        row = cursor.fetchone()

        if row is None:
            return None

        return self._row_to_task_record(row)

    def get_all_tasks(self) -> List[TaskRecord]:
        """Get all tasks ordered by creation time (latest first)

        Returns:
            List of TaskRecord objects
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC")
        rows = cursor.fetchall()

        return [self._row_to_task_record(row) for row in rows]

    def update_task_status(
        self,
        task_id: int,
        status: TaskStatus,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
        exit_code: Optional[int] = None,
        error_message: Optional[str] = None,
        message: Optional[str] = None,
    ):
        """Update task status and timestamps

        Args:
            task_id: Task ID to update
            status: New task status
            started_at: Task start time (optional)
            finished_at: Task completion time (optional)
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

        params.append(task_id)

        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self.conn.commit()

    def update_task_counts(
        self,
        task_id: int,
        case_count: Optional[int] = None,
        poc_count: Optional[int] = None,
    ):
        """Update task case and POC counts

        Args:
            task_id: Task ID to update
            case_count: Number of test cases (optional)
            poc_count: Number of POCs found (optional)
        """
        cursor = self.conn.cursor()

        updates = []
        params = []

        if case_count is not None:
            updates.append("case_count = ?")
            params.append(case_count)

        if poc_count is not None:
            updates.append("poc_count = ?")
            params.append(poc_count)

        if not updates:
            return

        params.append(task_id)

        query = f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?"
        cursor.execute(query, params)
        self.conn.commit()

    def update_task_compile_failed(self, task_id: int, compile_failed: Optional[int]):
        """Update task compile_failed count.

        Args:
            task_id: Task ID to update
            compile_failed: CompileFailed count, or None to clear
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tasks SET compile_failed = ? WHERE id = ?",
            (compile_failed, task_id),
        )
        self.conn.commit()

    def update_task_priority(self, task_id: int, priority: int):
        """Update task priority."""
        cursor = self.conn.cursor()
        cursor.execute(
            "UPDATE tasks SET priority = ? WHERE id = ?", (priority, task_id)
        )
        self.conn.commit()

    def update_task_pid(self, task_id: int, pid: int):
        """Update task process ID

        Args:
            task_id: Task ID to update
            pid: Process ID
        """
        cursor = self.conn.cursor()
        cursor.execute("UPDATE tasks SET pid = ? WHERE id = ?", (pid, task_id))
        self.conn.commit()

    def reset_task_for_retry(self, task_id: int):
        """Reset task state for retry

        Resets task to pending status and clears execution results.
        Keeps crate_name, version, and workspace paths intact.

        Args:
            task_id: Task ID to reset
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tasks SET
                status = ?,
                started_at = NULL,
                finished_at = NULL,
                exit_code = NULL,
                error_message = NULL,
                message = NULL,
                case_count = 0,
                poc_count = 0,
                compile_failed = 0,
                pid = NULL,
                runner_id = NULL,
                lease_token = NULL,
                lease_expires_at = NULL,
                last_event_seq = 0,
                cancel_requested = 0
            WHERE id = ?
        """,
            (TaskStatus.PENDING.value, task_id),
        )
        cursor.execute(
            "DELETE FROM task_log_chunk_sequences WHERE task_id = ?",
            (task_id,),
        )
        self.conn.commit()

    def reset_task_log_chunk_sequences(self, task_id: int):
        """Clear per-log chunk sequence tracking for a task."""
        cursor = self.conn.cursor()
        cursor.execute(
            "DELETE FROM task_log_chunk_sequences WHERE task_id = ?",
            (task_id,),
        )
        self.conn.commit()

    def get_tasks_by_status(self, status: TaskStatus) -> List[TaskRecord]:
        """Get tasks filtered by status

        Args:
            status: Status to filter by

        Returns:
            List of TaskRecord objects with matching status
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC",
            (status.value,),
        )
        rows = cursor.fetchall()

        return [self._row_to_task_record(row) for row in rows]

    def get_pending_tasks_ordered(self) -> List[TaskRecord]:
        """Get pending tasks ordered by priority (high first), then creation time."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY priority DESC, created_at ASC",
            (TaskStatus.PENDING.value,),
        )
        rows = cursor.fetchall()
        return [self._row_to_task_record(row) for row in rows]

    def _row_to_task_record(self, row: sqlite3.Row) -> TaskRecord:
        """Convert a database row to a TaskRecord

        Args:
            row: Database row

        Returns:
            TaskRecord object
        """
        return TaskRecord(
            id=row["id"],
            crate_name=row["crate_name"],
            version=row["version"],
            workspace_path=row["workspace_path"],
            stdout_log=row["stdout_log"],
            stderr_log=row["stderr_log"],
            status=TaskStatus(row["status"]),
            created_at=self._parse_datetime(row["created_at"]),
            started_at=(
                self._parse_datetime(row["started_at"]) if row["started_at"] else None
            ),
            finished_at=(
                self._parse_datetime(row["finished_at"]) if row["finished_at"] else None
            ),
            case_count=row["case_count"],
            poc_count=row["poc_count"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            error_message=row["error_message"],
            message=row["message"],
            memory_used_mb=row["memory_used_mb"],
            compile_failed=row["compile_failed"],
            priority=row["priority"],
            runner_id=row["runner_id"],
            lease_token=row["lease_token"],
            lease_expires_at=(
                self._parse_datetime(row["lease_expires_at"])
                if row["lease_expires_at"]
                else None
            ),
            attempt=row["attempt"],
            last_event_seq=row["last_event_seq"],
            cancel_requested=bool(row["cancel_requested"]),
        )

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

    def extend_runner_task_leases(self, runner_id: str, lease_ttl_seconds: int) -> int:
        """Extend lease expiration of all RUNNING tasks for a runner."""
        now = datetime.now()
        lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        cursor = self.conn.cursor()
        cursor.execute(
            """
            UPDATE tasks
            SET lease_expires_at = ?
            WHERE status = ? AND runner_id = ?
            """,
            (lease_expires_at, TaskStatus.RUNNING.value, runner_id),
        )
        self.conn.commit()
        return cursor.rowcount

    def claim_pending_task(
        self, runner_id: str, lease_ttl_seconds: int
    ) -> Optional[TaskRecord]:
        """Atomically claim one pending task for a runner."""
        now = datetime.now()
        lease_token = secrets.token_urlsafe(24)
        lease_expires_at = now + timedelta(seconds=lease_ttl_seconds)
        cursor = self.conn.cursor()

        try:
            cursor.execute("BEGIN IMMEDIATE")
            row = cursor.execute(
                """
                SELECT id
                FROM tasks
                WHERE status = ?
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                (TaskStatus.PENDING.value,),
            ).fetchone()

            if row is None:
                self.conn.commit()
                return None

            cursor.execute(
                """
                UPDATE tasks
                SET status = ?,
                    runner_id = ?,
                    lease_token = ?,
                    lease_expires_at = ?,
                    started_at = ?,
                    finished_at = NULL,
                    exit_code = NULL,
                    error_message = NULL,
                    message = NULL,
                    case_count = 0,
                    poc_count = 0,
                    compile_failed = 0,
                    pid = NULL,
                    last_event_seq = 0,
                    cancel_requested = 0
                WHERE id = ? AND status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    runner_id,
                    lease_token,
                    lease_expires_at,
                    now,
                    row["id"],
                    TaskStatus.PENDING.value,
                ),
            )

            if cursor.rowcount == 0:
                self.conn.commit()
                return None

            self.conn.commit()
            return self.get_task(row["id"])
        except Exception:
            self.conn.rollback()
            raise

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

    def delete_task(self, task_id: int) -> bool:
        """Delete a task from database

        Args:
            task_id: Task ID to delete

        Returns:
            True if task was deleted, False if task not found
        """
        cursor = self.conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        self.conn.commit()
        return cursor.rowcount > 0

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None
