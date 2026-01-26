"""Database layer for task persistence using SQLite."""
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class TaskRecord:
    """Represents a task record in the database."""
    id: int
    title: str
    description: str
    status: str
    case_count: int
    poc_count: int
    cases_completed: int
    pocs_completed: int
    pid: Optional[int]
    error_message: Optional[str]
    result_summary: Optional[str]
    output_dir: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    updated_at: datetime


class Database:
    """SQLite database manager for experiment tasks."""

    def __init__(self, db_path: str = "experiments.db"):
        """Initialize database connection.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

    def init_db(self) -> None:
        """Initialize database schema with tasks table and indexes."""
        cursor = self.conn.cursor()

        # Create tasks table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                case_count INTEGER NOT NULL,
                poc_count INTEGER NOT NULL,
                cases_completed INTEGER NOT NULL DEFAULT 0,
                pocs_completed INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                error_message TEXT,
                result_summary TEXT,
                output_dir TEXT,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TIMESTAMP,
                completed_at TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for common queries
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON tasks(created_at DESC)
        """)

        self.conn.commit()

    def create_task(
        self,
        title: str,
        description: str,
        case_count: int,
        poc_count: int,
        output_dir: Optional[str] = None
    ) -> int:
        """Create a new task.

        Args:
            title: Task title
            description: Task description
            case_count: Number of test cases
            poc_count: Number of POCs
            output_dir: Optional output directory path

        Returns:
            ID of created task
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO tasks (title, description, case_count, poc_count, output_dir)
            VALUES (?, ?, ?, ?, ?)
        """, (title, description, case_count, poc_count, output_dir))

        self.conn.commit()
        return cursor.lastrowid

    def get_task(self, task_id: int) -> Optional[TaskRecord]:
        """Get a task by ID.

        Args:
            task_id: Task ID

        Returns:
            TaskRecord if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()

        if row:
            return self._row_to_task(row)
        return None

    def get_all_tasks(self) -> list[TaskRecord]:
        """Get all tasks ordered by created_at DESC (newest first).

        Returns:
            List of TaskRecord objects
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tasks ORDER BY created_at DESC, id DESC")
        rows = cursor.fetchall()

        return [self._row_to_task(row) for row in rows]

    def get_tasks_by_status(self, status: str) -> list[TaskRecord]:
        """Get tasks filtered by status.

        Args:
            status: Task status (pending, running, completed, failed)

        Returns:
            List of TaskRecord objects
        """
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC, id DESC",
            (status,)
        )
        rows = cursor.fetchall()

        return [self._row_to_task(row) for row in rows]

    def update_task_status(
        self,
        task_id: int,
        status: str,
        error_message: Optional[str] = None,
        result_summary: Optional[str] = None
    ) -> None:
        """Update task status and related fields.

        Args:
            task_id: Task ID
            status: New status (running, completed, failed)
            error_message: Optional error message for failed tasks
            result_summary: Optional result summary for completed tasks
        """
        cursor = self.conn.cursor()

        # Determine timestamp fields to update based on status
        if status == "running":
            cursor.execute("""
                UPDATE tasks
                SET status = ?,
                    started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, task_id))

        elif status in ("completed", "failed"):
            cursor.execute("""
                UPDATE tasks
                SET status = ?,
                    completed_at = CURRENT_TIMESTAMP,
                    error_message = ?,
                    result_summary = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, error_message, result_summary, task_id))

        else:
            cursor.execute("""
                UPDATE tasks
                SET status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, task_id))

        self.conn.commit()

    def update_task_counts(
        self,
        task_id: int,
        cases_completed: int,
        pocs_completed: int
    ) -> None:
        """Update task progress counts.

        Args:
            task_id: Task ID
            cases_completed: Number of test cases completed
            pocs_completed: Number of POCs completed
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tasks
            SET cases_completed = ?,
                pocs_completed = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (cases_completed, pocs_completed, task_id))

        self.conn.commit()

    def update_task_pid(self, task_id: int, pid: int) -> None:
        """Update task process ID.

        Args:
            task_id: Task ID
            pid: Process ID
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE tasks
            SET pid = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (pid, task_id))

        self.conn.commit()

    def _row_to_task(self, row: sqlite3.Row) -> TaskRecord:
        """Convert a database row to TaskRecord.

        Args:
            row: sqlite3.Row object

        Returns:
            TaskRecord object
        """
        return TaskRecord(
            id=row['id'],
            title=row['title'],
            description=row['description'],
            status=row['status'],
            case_count=row['case_count'],
            poc_count=row['poc_count'],
            cases_completed=row['cases_completed'],
            pocs_completed=row['pocs_completed'],
            pid=row['pid'],
            error_message=row['error_message'],
            result_summary=row['result_summary'],
            output_dir=row['output_dir'],
            created_at=datetime.fromisoformat(row['created_at']),
            started_at=datetime.fromisoformat(row['started_at']) if row['started_at'] else None,
            completed_at=datetime.fromisoformat(row['completed_at']) if row['completed_at'] else None,
            updated_at=datetime.fromisoformat(row['updated_at'])
        )

    def close(self) -> None:
        """Close database connection."""
        self.conn.close()
