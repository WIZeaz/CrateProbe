import uvicorn
import logging
import base64
import uuid
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import RedirectResponse, PlainTextResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from pathlib import Path
import asyncio
from app.config import Config
from app.database import Database, TaskRecord
from core.models import TaskStatus
from runner.crates_api import CratesAPI, CrateNotFoundError
from app.services.scheduler import TaskScheduler
from app.services.system_monitor import SystemMonitor
from app.services.runner_metrics_store import RunnerMetricsStore, RunnerMetricPoint
from app.security import generate_runner_token, generate_salt, hash_token, verify_token
from app.utils.file_utils import read_last_n_lines
from app.utils.file_utils import FileNotFoundError as CustomFileNotFoundError
from app.api.websocket import get_manager

logger = logging.getLogger(__name__)


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
    runner_id: Optional[str]


class BatchPriorityRequest(BaseModel):
    task_ids: List[int]
    priority: int


class CreateRunnerRequest(BaseModel):
    runner_id: str


class RunnerResponse(BaseModel):
    runner_id: str
    enabled: bool
    created_at: str
    last_seen_at: Optional[str]


class RunnerCreateResponse(RunnerResponse):
    token: str
    token_hint: str


class ClaimTaskResponse(BaseModel):
    id: int
    crate_name: str
    version: str
    status: str
    runner_id: Optional[str]
    lease_token: str
    lease_expires_at: Optional[str]


class ClaimTaskRequest(BaseModel):
    runner_id: Optional[str] = None
    jobs: int = Field(ge=0)
    max_jobs: int = Field(ge=1)


class RunnerTaskEventRequest(BaseModel):
    lease_token: str
    event_seq: int
    event_type: str
    exit_code: Optional[int] = None
    message: Optional[str] = None
    case_count: Optional[int] = None
    poc_count: Optional[int] = None
    compile_failed: Optional[int] = None


class RunnerTaskLogChunkRequest(BaseModel):
    lease_token: str
    chunk_seq: int
    content: str


class RunnerMetricsRequest(BaseModel):
    timestamp: Optional[str] = None
    cpu_percent: float = Field(ge=0.0, le=100.0)
    memory_percent: float = Field(ge=0.0, le=100.0)
    disk_percent: float = Field(ge=0.0, le=100.0)
    active_tasks: int = Field(ge=0)


class RunnerLatestMetricsResponse(BaseModel):
    timestamp: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    active_tasks: int


class RunnerOverviewResponse(BaseModel):
    runner_id: str
    enabled: bool
    created_at: str
    last_seen_at: Optional[str]
    health_status: Literal["online", "offline", "disabled"]
    latest_metrics: Optional[RunnerLatestMetricsResponse]


class RunnerMetricsQueryResponse(BaseModel):
    runner: RunnerOverviewResponse
    window: Literal["1h", "6h", "24h"]
    latest: Optional[RunnerLatestMetricsResponse]
    series: List[RunnerLatestMetricsResponse]


RUNNER_CHUNK_LOG_TYPES = {"stdout", "stderr", "runner", "miri_report", "stats-yaml"}


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


def _clear_task_logs(task: TaskRecord, config: Config) -> None:
    for log_name, resolver in LOG_PATH_RESOLVERS.items():
        log_path = resolver(task, config)
        if not log_path.exists() or not log_path.is_file():
            continue
        log_path.unlink()
        logger.info(
            "task log cleared for retry",
            extra={"task_id": task.id, "log_type": log_name, "path": str(log_path)},
        )
    # Also remove the testgen directory so stale counts do not persist
    testgen_dir = Path(task.workspace_path) / "testgen"
    if testgen_dir.exists():
        import shutil

        shutil.rmtree(testgen_dir)
        logger.info(
            "task testgen directory cleared for retry",
            extra={"task_id": task.id, "path": str(testgen_dir)},
        )


def create_app(config: Config, db_path: str) -> FastAPI:
    """Create FastAPI application"""

    # Initialize database
    db = Database(db_path)
    db.init_db()

    # Create scheduler
    scheduler = TaskScheduler(config, db)
    metrics_store = RunnerMetricsStore(max_age=timedelta(hours=24))

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
        title="CrateProbe",
        description="Automated Rust crate testing platform",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.scheduler = scheduler

    def require_admin_token(
        x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    ) -> None:
        if not config.admin_token or x_admin_token != config.admin_token:
            raise HTTPException(status_code=403, detail="Forbidden")

    def token_hint(token: str) -> str:
        return f"****{token[-4:]}"

    def _runner_to_response(runner) -> RunnerResponse:
        return RunnerResponse(
            runner_id=runner.runner_id,
            enabled=runner.enabled,
            created_at=runner.created_at.isoformat(),
            last_seen_at=(
                runner.last_seen_at.isoformat() if runner.last_seen_at else None
            ),
        )

    def _extract_bearer_token(authorization: Optional[str]) -> Optional[str]:
        if not authorization:
            return None
        parts = authorization.strip().split(" ", 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        token = parts[1].strip()
        return token or None

    def _request_id_from_header(x_request_id: Optional[str]) -> str:
        if x_request_id and x_request_id.strip():
            return x_request_id.strip()
        return uuid.uuid4().hex[:12]

    def require_runner_auth(
        runner_id: str,
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> None:
        runner = db.get_runner_by_runner_id(runner_id)
        if runner is None or not runner.enabled:
            raise HTTPException(status_code=403, detail="Forbidden")

        token = _extract_bearer_token(authorization)
        if token is None:
            raise HTTPException(status_code=403, detail="Forbidden")

        try:
            salt = base64.urlsafe_b64decode(runner.token_salt.encode("ascii"))
        except Exception:
            raise HTTPException(status_code=403, detail="Forbidden")

        if not verify_token(token, salt, runner.token_hash):
            raise HTTPException(status_code=403, detail="Forbidden")

    def require_task_lease(
        task_id: int,
        runner_id: str,
        lease_token: str,
        *,
        request_id: str,
        event_seq: Optional[int] = None,
        log_type: Optional[str] = None,
        chunk_seq: Optional[int] = None,
    ) -> TaskRecord:
        task = db.get_task(task_id)
        if task is None:
            if log_type is not None:
                logger.warning(
                    "runner task not found for log ingest",
                    extra={
                        "request_id": request_id,
                        "runner_id": runner_id,
                        "task_id": task_id,
                        "log_type": log_type,
                        "chunk_seq": chunk_seq,
                    },
                )
            else:
                logger.warning(
                    "runner task not found for event ingest",
                    extra={
                        "request_id": request_id,
                        "runner_id": runner_id,
                        "task_id": task_id,
                        "event_seq": event_seq,
                    },
                )
            raise HTTPException(status_code=404, detail="Task not found")

        if (
            task.runner_id != runner_id
            or task.lease_token is None
            or lease_token != task.lease_token
        ):
            logger.warning(
                "lease token mismatch",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "task_id": task_id,
                    "event_seq": event_seq,
                    "log_type": log_type,
                    "chunk_seq": chunk_seq,
                },
            )
            raise HTTPException(status_code=409, detail="Lease token mismatch")

        return task

    def _parse_metric_timestamp(value: Optional[str]) -> datetime:
        if not value:
            return datetime.now()
        try:
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is not None:
                return parsed.replace(tzinfo=None)
            return parsed
        except Exception:
            return datetime.now()

    def _health_status(runner) -> Literal["online", "offline", "disabled"]:
        if not runner.enabled:
            return "disabled"
        if not runner.last_seen_at:
            return "offline"
        age = (datetime.now() - runner.last_seen_at).total_seconds()
        if age > config.runner_offline_seconds:
            return "offline"
        return "online"

    def _metric_to_response(
        metric: Optional[RunnerMetricPoint],
    ) -> Optional[RunnerLatestMetricsResponse]:
        if metric is None:
            return None
        return RunnerLatestMetricsResponse(
            timestamp=metric.ts.isoformat(),
            cpu_percent=metric.cpu_percent,
            memory_percent=metric.memory_percent,
            disk_percent=metric.disk_percent,
            active_tasks=metric.active_tasks,
        )

    def _window_to_timedelta(window: str) -> timedelta:
        mapping = {
            "1h": timedelta(hours=1),
            "6h": timedelta(hours=6),
            "24h": timedelta(hours=24),
        }
        value = mapping.get(window)
        if value is None:
            raise HTTPException(status_code=422, detail="Invalid window")
        return value

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
                config.workspace_path / "repos" / f"{request.crate_name}-{version}"
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

            created_task = db.get_task(task_id)
            if created_task is not None:
                payload = _task_to_dict(created_task)
                payload["type"] = "task_created"
                await ws_manager.broadcast_dashboard_update(payload)

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

    @app.post(
        "/api/admin/runners",
        response_model=RunnerCreateResponse,
        status_code=201,
        dependencies=[Depends(require_admin_token)],
    )
    async def create_runner(request: CreateRunnerRequest):
        existing = db.get_runner_by_runner_id(request.runner_id)
        if existing is not None:
            raise HTTPException(status_code=409, detail="Runner already exists")

        token = generate_runner_token()
        salt = generate_salt()
        token_hash = hash_token(token, salt)
        token_salt = base64.urlsafe_b64encode(salt).decode("ascii")
        runner = db.create_runner(request.runner_id, token_hash, token_salt)

        return RunnerCreateResponse(
            runner_id=runner.runner_id,
            enabled=runner.enabled,
            created_at=runner.created_at.isoformat(),
            last_seen_at=(
                runner.last_seen_at.isoformat() if runner.last_seen_at else None
            ),
            token=token,
            token_hint=token_hint(token),
        )

    @app.get(
        "/api/admin/runners",
        response_model=List[RunnerResponse],
        dependencies=[Depends(require_admin_token)],
    )
    async def list_runners():
        runners = db.list_runners()
        return [_runner_to_response(runner) for runner in runners]

    @app.head(
        "/api/admin/runners",
        dependencies=[Depends(require_admin_token)],
    )
    async def head_runners():
        return {}

    @app.delete(
        "/api/admin/runners/{runner_id}",
        response_model=RunnerResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def delete_runner(runner_id: str):
        runner = db.get_runner_by_runner_id(runner_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        response = _runner_to_response(runner)
        deleted = db.delete_runner(runner_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Runner not found")

        return response

    @app.post(
        "/api/admin/runners/{runner_id}/disable",
        response_model=RunnerResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def disable_runner(runner_id: str):
        runner = db.get_runner_by_runner_id(runner_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        db.disable_runner(runner_id)
        updated_runner = db.get_runner_by_runner_id(runner_id)
        if updated_runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        return _runner_to_response(updated_runner)

    @app.post(
        "/api/admin/runners/{runner_id}/enable",
        response_model=RunnerResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def enable_runner(runner_id: str):
        runner = db.get_runner_by_runner_id(runner_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        db.enable_runner(runner_id)
        updated_runner = db.get_runner_by_runner_id(runner_id)
        if updated_runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        return _runner_to_response(updated_runner)

    @app.post("/api/runners/{runner_id}/heartbeat")
    async def runner_heartbeat(
        runner_id: str,
        _auth: None = Depends(require_runner_auth),
    ):
        db.touch_runner_heartbeat(runner_id)
        db.extend_runner_task_leases(runner_id, config.lease_ttl_seconds)
        return {"success": True}

    @app.post("/api/runners/{runner_id}/metrics")
    async def ingest_runner_metrics(
        runner_id: str,
        request: RunnerMetricsRequest,
        _auth: None = Depends(require_runner_auth),
    ):
        timestamp = _parse_metric_timestamp(request.timestamp)
        await metrics_store.append(
            runner_id=runner_id,
            ts=timestamp,
            cpu_percent=request.cpu_percent,
            memory_percent=request.memory_percent,
            disk_percent=request.disk_percent,
            active_tasks=request.active_tasks,
        )
        return {"success": True}

    @app.get(
        "/api/admin/runners/overview",
        response_model=List[RunnerOverviewResponse],
        dependencies=[Depends(require_admin_token)],
    )
    async def get_runner_overview():
        runners = db.list_runners()
        result: List[RunnerOverviewResponse] = []
        for runner in runners:
            latest = await metrics_store.get_latest(runner.runner_id)
            result.append(
                RunnerOverviewResponse(
                    runner_id=runner.runner_id,
                    enabled=runner.enabled,
                    created_at=runner.created_at.isoformat(),
                    last_seen_at=(
                        runner.last_seen_at.isoformat() if runner.last_seen_at else None
                    ),
                    health_status=_health_status(runner),
                    latest_metrics=_metric_to_response(latest),
                )
            )
        return result

    @app.get(
        "/api/admin/runners/{runner_id}/metrics",
        response_model=RunnerMetricsQueryResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def get_runner_metrics(
        runner_id: str,
        window: Literal["1h", "6h", "24h"] = Query(default="1h"),
    ):
        runner = db.get_runner_by_runner_id(runner_id)
        if runner is None:
            raise HTTPException(status_code=404, detail="Runner not found")

        window_delta = _window_to_timedelta(window)
        latest = await metrics_store.get_latest(runner_id)
        series = await metrics_store.get_series(runner_id, window_delta)
        series = sorted(series, key=lambda point: point.ts)

        runner_info = RunnerOverviewResponse(
            runner_id=runner.runner_id,
            enabled=runner.enabled,
            created_at=runner.created_at.isoformat(),
            last_seen_at=(
                runner.last_seen_at.isoformat() if runner.last_seen_at else None
            ),
            health_status=_health_status(runner),
            latest_metrics=_metric_to_response(latest),
        )
        series_response = [_metric_to_response(point) for point in series]
        return RunnerMetricsQueryResponse(
            runner=runner_info,
            window=window,
            latest=_metric_to_response(latest),
            series=[point for point in series_response if point is not None],
        )

    @app.post(
        "/api/runners/{runner_id}/claim",
        response_model=ClaimTaskResponse,
        responses={204: {"description": "No pending tasks"}},
    )
    async def claim_task(
        runner_id: str,
        request: ClaimTaskRequest,
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)
        if request.max_jobs > config.claim_max_jobs_hard_limit:
            logger.warning(
                "invalid claim payload: max_jobs exceeds hard limit",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "max_jobs": request.max_jobs,
                },
            )
            raise HTTPException(
                status_code=422,
                detail="Invalid claim payload: max_jobs exceeds hard limit",
            )

        if request.jobs > request.max_jobs:
            logger.warning(
                "invalid claim payload: jobs cannot exceed max_jobs",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "jobs": request.jobs,
                    "max_jobs": request.max_jobs,
                },
            )
            raise HTTPException(
                status_code=422,
                detail="Invalid claim payload: jobs cannot exceed max_jobs",
            )

        if request.jobs >= request.max_jobs:
            return PlainTextResponse(status_code=204, content="")

        task = db.claim_pending_task(runner_id, config.lease_ttl_seconds)
        if task is None:
            return PlainTextResponse(status_code=204, content="")

        return ClaimTaskResponse(
            id=task.id,
            crate_name=task.crate_name,
            version=task.version,
            status=task.status.value,
            runner_id=task.runner_id,
            lease_token=task.lease_token or "",
            lease_expires_at=(
                task.lease_expires_at.isoformat() if task.lease_expires_at else None
            ),
        )

    @app.post("/api/runners/{runner_id}/tasks/{task_id}/events")
    async def ingest_runner_task_event(
        runner_id: str,
        task_id: int,
        request: RunnerTaskEventRequest,
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)
        require_task_lease(
            task_id,
            runner_id,
            request.lease_token,
            request_id=request_id,
            event_seq=request.event_seq,
        )
        applied = db.apply_task_event(task_id, request.event_seq, request.event_type)
        if applied is None:
            logger.warning(
                "runner task not found for event ingest",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "task_id": task_id,
                    "event_seq": request.event_seq,
                },
            )
            raise HTTPException(status_code=404, detail="Task not found")

        if not applied:
            logger.info(
                "runner task event not applied",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "task_id": task_id,
                    "event_seq": request.event_seq,
                },
            )

        if applied and request.event_type not in ("started", "progress"):
            terminal_status = (
                TaskStatus.COMPLETED
                if request.event_type == "completed"
                else TaskStatus.FAILED
            )
            db.update_task_status(
                task_id,
                terminal_status,
                exit_code=request.exit_code,
                message=request.message,
            )
            if request.case_count is not None or request.poc_count is not None:
                db.update_task_counts(
                    task_id,
                    case_count=request.case_count,
                    poc_count=request.poc_count,
                )
            if request.compile_failed is not None:
                db.update_task_compile_failed(task_id, request.compile_failed)

        if applied and request.event_type == "progress":
            if request.case_count is not None or request.poc_count is not None:
                db.update_task_counts(
                    task_id,
                    case_count=request.case_count,
                    poc_count=request.poc_count,
                )

        if applied:
            updated_task = db.get_task(task_id)
            if updated_task is not None:
                task_payload = _task_to_dict(updated_task)
                task_payload["type"] = "task_update"
                await ws_manager.broadcast_task_update(task_id, task_payload)

                dashboard_payload = _task_to_dict(updated_task)
                dashboard_payload["type"] = (
                    "task_completed"
                    if request.event_type not in ("started", "progress")
                    else "task_update"
                )
                await ws_manager.broadcast_dashboard_update(dashboard_payload)

        return {"applied": applied}

    @app.post("/api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks")
    async def ingest_runner_task_log_chunk(
        runner_id: str,
        task_id: int,
        log_type: str,
        request: RunnerTaskLogChunkRequest,
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)
        if log_type not in RUNNER_CHUNK_LOG_TYPES:
            logger.warning(
                "unknown log type on runner log ingest",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "task_id": task_id,
                    "log_type": log_type,
                    "chunk_seq": request.chunk_seq,
                },
            )
            raise HTTPException(status_code=404, detail="Unknown log type")

        task = require_task_lease(
            task_id,
            runner_id,
            request.lease_token,
            request_id=request_id,
            log_type=log_type,
            chunk_seq=request.chunk_seq,
        )

        should_append = db.record_task_log_chunk(task_id, log_type, request.chunk_seq)
        if should_append:
            log_path = LOG_PATH_RESOLVERS[log_type](task, config)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(request.content)

        return {"appended": should_append}

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

    @app.post("/api/tasks/{task_id}/retry", response_model=TaskResponse)
    async def retry_task(task_id: int):
        """Retry/re-execute a task by resetting it to pending state"""
        task = db.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        # Cannot retry a task that's currently running
        if task.status == TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Cannot retry a running task")

        _clear_task_logs(task, config)

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
                _clear_task_logs(task, config)
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
        "runner_id": task.runner_id,
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
        runner_id=task.runner_id,
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
