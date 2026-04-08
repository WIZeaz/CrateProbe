import uvicorn
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse, PlainTextResponse
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
import asyncio
from app.config import Config
from app.database import Database, TaskRecord
from app.models import TaskStatus
from app.services.crates_api import CratesAPI, CrateNotFoundError
from app.services.scheduler import TaskScheduler
from app.services.system_monitor import SystemMonitor
from app.utils.file_utils import read_last_n_lines
from app.utils.file_utils import FileNotFoundError as CustomFileNotFoundError
from app.api.websocket import get_manager


# Request/Response models
class CreateTaskRequest(BaseModel):
    crate_name: str
    version: Optional[str] = None


class BatchTaskRequest(BaseModel):
    task_ids: List[int]


class TaskResponse(BaseModel):
    task_id: int
    crate_name: str
    version: str
    status: str


class TaskDetailResponse(BaseModel):
    id: int
    crate_name: str
    version: str
    status: str
    exit_code: Optional[int]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    case_count: int
    poc_count: int
    error_message: Optional[str]
    message: Optional[str]
    compile_failed: Optional[int]
    priority: Optional[int]


class BatchPriorityRequest(BaseModel):
    task_ids: List[int]
    priority: int


LOG_PATH_RESOLVERS = {
    "stdout": lambda task, _cfg: Path(task.stdout_log),
    "stderr": lambda task, _cfg: Path(task.stderr_log),
    "runner": lambda task, cfg: cfg.workspace_path / "logs" / f"{task.id}-runner.log",
    "miri_report": lambda task, _cfg: Path(task.workspace_path)
    / "testgen"
    / "miri_report.txt",
    "stats-yaml": lambda task, _cfg: Path(task.workspace_path)
    / "testgen"
    / "stats.yaml",
}


def create_app(config: Config, db_path: str) -> FastAPI:
    """Create FastAPI application"""

    # Initialize database
    db = Database(db_path)
    db.init_db()

    # Create scheduler
    scheduler = TaskScheduler(config, db)

    # Get WebSocket manager
    ws_manager = get_manager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        config.ensure_workspace_structure()
        # Recover orphaned tasks from previous server instance
        scheduler.recover_orphaned_tasks()
        # Start scheduler in background
        import asyncio

        scheduler_task = asyncio.create_task(scheduler.run())
        yield
        # Shutdown
        scheduler_task.cancel()
        try:
            await scheduler_task
        except asyncio.CancelledError:
            pass
        db.close()

    app = FastAPI(
        title="Experiment Platform",
        description="Automated Rust crate testing platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/docs")

    @app.post("/api/tasks", response_model=TaskResponse)
    async def create_task(request: CreateTaskRequest):
        """Create a new task"""
        crates_api = CratesAPI()

        try:
            # Get version if not specified
            version = request.version
            if not version:
                version = await crates_api.get_latest_version(request.crate_name)
            else:
                # Verify version exists
                exists = await crates_api.verify_version_exists(
                    request.crate_name, version
                )
                if not exists:
                    raise HTTPException(
                        status_code=400, detail=f"Version {version} not found"
                    )

            # Check if task already exists for this crate and version
            existing_task = db.get_task_by_crate_and_version(
                request.crate_name, version
            )
            if existing_task:
                return TaskResponse(
                    task_id=existing_task.id,
                    crate_name=existing_task.crate_name,
                    version=existing_task.version,
                    status=existing_task.status.value,
                )

            # Create workspace paths
            workspace_path = (
                config.workspace_path / "repos" /
                f"{request.crate_name}-{version}"
            )
            stdout_log = (
                config.workspace_path
                / "logs"
                / f"{request.crate_name}-{version}-stdout.log"
            )
            stderr_log = (
                config.workspace_path
                / "logs"
                / f"{request.crate_name}-{version}-stderr.log"
            )

            # Create task in database
            task_id = db.create_task(
                crate_name=request.crate_name,
                version=version,
                workspace_path=str(workspace_path),
                stdout_log=str(stdout_log),
                stderr_log=str(stderr_log),
            )

            return TaskResponse(
                task_id=task_id,
                crate_name=request.crate_name,
                version=version,
                status=TaskStatus.PENDING.value,
            )

        except CrateNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            await crates_api.close()

    @app.get("/api/tasks", response_model=List[TaskDetailResponse])
    async def get_all_tasks():
        """Get all tasks"""
        tasks = db.get_all_tasks()
        return [_task_to_response(task) for task in tasks]

    @app.get("/api/tasks/{task_id}", response_model=TaskDetailResponse)
    async def get_task(task_id: int):
        """Get task by ID"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return _task_to_response(task)

    @app.post("/api/tasks/{task_id}/cancel")
    async def cancel_running_task(task_id: int):
        """Cancel a running task (does not delete from database)"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status != TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Task is not running")

        await scheduler.cancel_task(task_id)
        return {"message": "Task cancelled"}

    @app.get("/api/tasks/{task_id}/stats")
    async def get_task_realtime_stats(task_id: int):
        """Get real-time test case and POC counts from testgen directory"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Build testgen directory path
        workspace_path = Path(task.workspace_path)
        testgen_dir = workspace_path / "testgen"

        case_count = 0
        poc_count = 0

        # Count test cases
        tests_dir = testgen_dir / "tests"
        if tests_dir.exists():
            case_count = len([d for d in tests_dir.iterdir() if d.is_dir()])

        # Count POCs
        poc_dir = testgen_dir / "poc"
        if poc_dir.exists():
            poc_count = len([d for d in poc_dir.iterdir() if d.is_dir()])

        return {"case_count": case_count, "poc_count": poc_count}

    @app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
    async def retry_task(task_id: int):
        """Retry/re-execute a task by resetting it to pending state"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Cannot retry a task that's currently running
        if task.status == TaskStatus.RUNNING:
            raise HTTPException(
                status_code=400, detail="Cannot retry a running task")

        # Reset task to pending state
        db.reset_task_for_retry(task_id)

        return TaskResponse(
            task_id=task_id,
            crate_name=task.crate_name,
            version=task.version,
            status=TaskStatus.PENDING.value,
        )

    @app.delete("/api/tasks/{task_id}")
    async def delete_task(task_id: int):
        """Delete a task from database (cannot delete running tasks)"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status == TaskStatus.RUNNING:
            raise HTTPException(
                status_code=400, detail="Cannot delete running task. Cancel it first."
            )

        deleted = db.delete_task(task_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Task not found")

        return {"message": "Task deleted"}

    @app.post("/api/tasks/batch-retry")
    async def batch_retry_tasks(request: BatchTaskRequest):
        """Batch retry multiple tasks"""
        results = {"retried": [], "skipped": [], "not_found": []}

        for task_id in request.task_ids:
            task = db.get_task(task_id)
            if not task:
                results["not_found"].append(task_id)
            elif task.status == TaskStatus.RUNNING:
                results["skipped"].append(task_id)
            else:
                db.reset_task_for_retry(task_id)
                results["retried"].append(task_id)

        return results

    @app.post("/api/tasks/batch-delete")
    async def batch_delete_tasks(request: BatchTaskRequest):
        """Batch delete multiple tasks"""
        results = {"deleted": [], "skipped": [], "not_found": []}

        for task_id in request.task_ids:
            task = db.get_task(task_id)
            if not task:
                results["not_found"].append(task_id)
            elif task.status == TaskStatus.RUNNING:
                results["skipped"].append(task_id)
            else:
                db.delete_task(task_id)
                results["deleted"].append(task_id)

        return results

    @app.post("/api/tasks/batch-priority")
    async def batch_set_priority(request: BatchPriorityRequest):
        """Batch set priority on pending tasks. Skips non-pending tasks."""
        results = {"updated": [], "skipped": [], "not_found": []}
        for task_id in request.task_ids:
            task = db.get_task(task_id)
            if not task:
                results["not_found"].append(task_id)
            elif task.status != TaskStatus.PENDING:
                results["skipped"].append(task_id)
            else:
                db.update_task_priority(task_id, request.priority)
                results["updated"].append(task_id)
        return results

    @app.post("/api/tasks/batch-cancel")
    async def batch_cancel_tasks(request: BatchTaskRequest):
        """Batch cancel running tasks."""
        results = {"cancelled": [], "skipped": [], "not_found": []}
        for task_id in request.task_ids:
            task = db.get_task(task_id)
            if not task:
                results["not_found"].append(task_id)
            elif task.status != TaskStatus.RUNNING:
                results["skipped"].append(task_id)
            else:
                await scheduler.cancel_task(task_id)
                results["cancelled"].append(task_id)
        return results

    @app.get("/api/queue")
    async def get_queue():
        """Get queue state."""
        running = db.get_tasks_by_status(TaskStatus.RUNNING)
        pending = db.get_pending_tasks_ordered()
        return {
            "running": [_task_to_dict(t) for t in running],
            "pending": [_task_to_dict(t) for t in pending],
        }

    @app.get("/api/dashboard/stats")
    async def get_dashboard_stats():
        """Get task statistics for dashboard"""
        all_tasks = db.get_all_tasks()

        stats = {
            "total": len(all_tasks),
            "pending": len([t for t in all_tasks if t.status == TaskStatus.PENDING]),
            "running": len([t for t in all_tasks if t.status == TaskStatus.RUNNING]),
            "completed": len(
                [t for t in all_tasks if t.status == TaskStatus.COMPLETED]
            ),
            "failed": len([t for t in all_tasks if t.status == TaskStatus.FAILED]),
            "cancelled": len(
                [t for t in all_tasks if t.status == TaskStatus.CANCELLED]
            ),
            "timeout": len([t for t in all_tasks if t.status == TaskStatus.TIMEOUT]),
            "oom": len([t for t in all_tasks if t.status == TaskStatus.OOM]),
        }

        return stats

    @app.get("/api/dashboard/system")
    async def get_system_stats():
        """Get system resource statistics"""
        monitor = SystemMonitor()
        return monitor.get_system_stats()

    @app.get("/api/tasks/{task_id}/logs/{log_name}")
    async def get_task_log(
        task_id: int, log_name: str, lines: int = Query(default=1000, ge=0)
    ):
        """Get last N lines of any task log by name"""
        if log_name not in LOG_PATH_RESOLVERS:
            raise HTTPException(status_code=404, detail="Unknown log type")

        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        log_path = LOG_PATH_RESOLVERS[log_name](task, config)

        try:
            log_lines = read_last_n_lines(str(log_path), lines)
            return {"lines": log_lines}
        except CustomFileNotFoundError:
            raise HTTPException(status_code=404, detail="Log file not found")

    @app.get(
        "/api/tasks/{task_id}/logs/{log_name}/raw",
        response_class=PlainTextResponse,
    )
    async def get_task_log_raw(task_id: int, log_name: str):
        """Download full content of any task log by name"""
        if log_name not in LOG_PATH_RESOLVERS:
            raise HTTPException(status_code=404, detail="Unknown log type")

        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        log_path = LOG_PATH_RESOLVERS[log_name](task, config)

        if not log_path.exists():
            raise HTTPException(status_code=404, detail="Log file not found")

        return PlainTextResponse(log_path.read_text(encoding="utf-8", errors="replace"))

    @app.websocket("/ws/tasks/{task_id}")
    async def websocket_task_endpoint(websocket: WebSocket, task_id: int):
        """WebSocket endpoint for real-time task status updates"""
        # Check if task exists
        task = db.get_task(task_id)
        if not task:
            await websocket.close(code=1000)
            return

        # Connect and send initial state
        await ws_manager.connect_task(task_id, websocket)

        try:
            # Send initial task state
            await websocket.send_json(_task_to_dict(task))

            # Keep connection alive and wait for client messages or disconnection
            while True:
                try:
                    # Wait for a message from client (or disconnection)
                    await websocket.receive_text()
                except WebSocketDisconnect:
                    break
        except Exception:
            pass
        finally:
            ws_manager.disconnect_task(task_id, websocket)

    @app.websocket("/ws/dashboard")
    async def websocket_dashboard_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time dashboard updates"""
        await ws_manager.connect_dashboard(websocket)

        try:
            # Send initial dashboard stats
            all_tasks = db.get_all_tasks()
            stats = {
                "total": len(all_tasks),
                "pending": len(
                    [t for t in all_tasks if t.status == TaskStatus.PENDING]
                ),
                "running": len(
                    [t for t in all_tasks if t.status == TaskStatus.RUNNING]
                ),
                "completed": len(
                    [t for t in all_tasks if t.status == TaskStatus.COMPLETED]
                ),
                "failed": len([t for t in all_tasks if t.status == TaskStatus.FAILED]),
                "cancelled": len(
                    [t for t in all_tasks if t.status == TaskStatus.CANCELLED]
                ),
                "timeout": len(
                    [t for t in all_tasks if t.status == TaskStatus.TIMEOUT]
                ),
                "oom": len([t for t in all_tasks if t.status == TaskStatus.OOM]),
            }
            await websocket.send_json(stats)

            # Keep connection alive and wait for client messages or disconnection
            while True:
                try:
                    # Wait for a message from client (or disconnection)
                    await websocket.receive_text()
                except WebSocketDisconnect:
                    break
        except Exception:
            pass
        finally:
            ws_manager.disconnect_dashboard(websocket)

    return app


def _task_to_dict(task: TaskRecord) -> dict:
    """Convert TaskRecord to dictionary for WebSocket"""
    return {
        "id": task.id,
        "crate_name": task.crate_name,
        "version": task.version,
        "status": task.status.value,
        "exit_code": task.exit_code,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
        "case_count": task.case_count or 0,
        "poc_count": task.poc_count or 0,
        "error_message": task.error_message,
        "message": task.message,
        "compile_failed": task.compile_failed,
        "priority": task.priority,
    }


def _task_to_response(task: TaskRecord) -> TaskDetailResponse:
    """Convert TaskRecord to response model"""
    return TaskDetailResponse(
        id=task.id,
        crate_name=task.crate_name,
        version=task.version,
        status=task.status.value,
        exit_code=task.exit_code,
        created_at=task.created_at.isoformat() if task.created_at else None,
        started_at=task.started_at.isoformat() if task.started_at else None,
        finished_at=task.finished_at.isoformat() if task.finished_at else None,
        case_count=task.case_count or 0,
        poc_count=task.poc_count or 0,
        error_message=task.error_message,
        message=task.message,
        compile_failed=task.compile_failed,
        priority=task.priority,
    )


# Load config from project root
config_path = Path(__file__).parent.parent.parent / "config.toml"
config = Config.from_file(str(config_path))
app = create_app(config, str(config.get_db_full_path()))

# Main entry point
if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=config.server_host,
        port=config.server_port,
        log_level=config.log_level.lower(),
    )
