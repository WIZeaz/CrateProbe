import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from typing import Optional, List
from app.config import Config
from app.database import Database, TaskRecord
from app.models import TaskStatus
from app.services.crates_api import CratesAPI, CrateNotFoundError
from app.services.scheduler import TaskScheduler


# Request/Response models
class CreateTaskRequest(BaseModel):
    crate_name: str
    version: Optional[str] = None


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


def create_app(config: Config, db_path: str) -> FastAPI:
    """Create FastAPI application"""

    # Initialize database
    db = Database(db_path)
    db.init_db()

    # Create scheduler
    scheduler = TaskScheduler(config, db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        config.ensure_workspace_structure()
        # Start scheduler in background
        import asyncio
        scheduler_task = asyncio.create_task(scheduler.run())
        yield
        # Shutdown
        scheduler_task.cancel()
        await db.close()

    app = FastAPI(
        title="Experiment Platform",
        description="Automated Rust crate testing platform",
        version="1.0.0",
        lifespan=lifespan
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
                exists = await crates_api.verify_version_exists(request.crate_name, version)
                if not exists:
                    raise HTTPException(status_code=400, detail=f"Version {version} not found")

            # Create workspace paths
            workspace_path = config.workspace_path / "repos" / f"{request.crate_name}-{version}"
            stdout_log = config.workspace_path / "logs" / f"{request.crate_name}-{version}-stdout.log"
            stderr_log = config.workspace_path / "logs" / f"{request.crate_name}-{version}-stderr.log"

            # Create task in database
            task_id = db.create_task(
                crate_name=request.crate_name,
                version=version,
                workspace_path=str(workspace_path),
                stdout_log=str(stdout_log),
                stderr_log=str(stderr_log)
            )

            return TaskResponse(
                task_id=task_id,
                crate_name=request.crate_name,
                version=version,
                status=TaskStatus.PENDING.value
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

    @app.delete("/api/tasks/{task_id}")
    async def cancel_task(task_id: int):
        """Cancel a running task"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task.status != TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Task is not running")

        await scheduler.cancel_task(task_id)
        return {"message": "Task cancelled"}

    return app


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
        error_message=task.error_message
    )


# Main entry point
if __name__ == "__main__":
    import uvicorn

    config = Config.from_file("config.toml")
    app = create_app(config, str(config.get_db_full_path()))

    uvicorn.run(
        app,
        host=config.server_host,
        port=config.server_port,
        log_level=config.log_level.lower()
    )
