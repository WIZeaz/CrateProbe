"""Database layer for experiment tracking"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from app.models import TaskStatus


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
        # Backward-compatible migration for existing databases.
        columns = {
            row["name"] for row in cursor.execute("PRAGMA table_info(tasks)").fetchall()
        }
        if "message" not in columns:
            cursor.execute("ALTER TABLE tasks ADD COLUMN message TEXT")
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at DESC)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
        """)
        self.conn.commit()

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
                pid = NULL
            WHERE id = ?
        """,
            (TaskStatus.PENDING.value, task_id),
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
        )

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
