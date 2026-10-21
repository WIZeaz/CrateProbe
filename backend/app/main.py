import uvicorn
import logging
import base64
import json
import uuid
import zipfile
from datetime import datetime, timedelta
from contextlib import asynccontextmanager
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import Response
from fastapi.responses import FileResponse, RedirectResponse, PlainTextResponse
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from pathlib import Path
import asyncio
from app.config import Config
from app.database import Database, JobRecord, TaskDefRecord
from core.models import TaskStatus
from core.task_spec import spec_to_dict
from runner.crates_api import CratesAPI, CrateNotFoundError
from runner.rate_limiter import AsyncTokenBucket
from runner.cache import TTLCache
from app.services.scheduler import TaskScheduler
from app.services.system_monitor import SystemMonitor
from app.services.runner_metrics_store import RunnerMetricsStore, RunnerMetricPoint
from app.services.task_store import (
    TaskStore,
    ZipValidationError,
    build_example_task_zip,
)
from app.services.job_files import (
    ArtifactTooLargeError,
    JobFileStore,
    PathSafetyError,
)
from app.security import generate_runner_token, generate_salt, hash_token, verify_token
from app.utils.file_utils import FileNotFoundError as CustomFileNotFoundError
from app.api.websocket import get_manager

logger = logging.getLogger(__name__)


# Shared across all API requests to enforce a single process-wide rate limit
# and metadata cache for crates.io API calls.
_crates_api_rate_limiter = AsyncTokenBucket(rate=1.0)
_crates_api_cache = TTLCache(ttl_seconds=300.0)


# Request/Response models
class CreateJobRequest(BaseModel):
    crate_name: str
    version: Optional[str] = None
    task_id: int


class BatchJobRequest(BaseModel):
    job_ids: List[int]


class TaskDefinitionResponse(BaseModel):
    id: int
    name: str
    desc: Optional[str]
    sha256: str
    created_at: str
    updated_at: Optional[str]
    spec: dict


class TaskUploadResponse(TaskDefinitionResponse):
    existed: bool


class JobResponse(BaseModel):
    job_id: int
    crate_name: str
    version: str
    status: str


class JobDetailResponse(BaseModel):
    id: int
    crate_name: str
    version: str
    status: str
    exit_code: Optional[int]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    error_message: Optional[str]
    message: Optional[str]
    priority: Optional[int]
    runner_id: Optional[str]
    task_id: int
    task_name: str
    task_sha256: str
    task_spec: dict


class BatchPriorityRequest(BaseModel):
    job_ids: List[int]
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


class ClaimJobResponse(BaseModel):
    job_id: int
    crate_name: str
    version: str
    status: str
    runner_id: Optional[str]
    lease_token: str
    lease_expires_at: Optional[str]
    task_id: int
    task_sha256: str


class ClaimJobRequest(BaseModel):
    runner_id: Optional[str] = None
    jobs: int = Field(ge=0)
    max_jobs: int = Field(ge=1)


class RunnerTaskSyncRequest(BaseModel):
    lease_token: str
    sync_seq: int
    status: str
    exit_code: Optional[int] = None
    message: Optional[str] = None


class RunnerFilePushRequest(BaseModel):
    lease_token: str
    path: str
    mode: Literal["append", "full"]
    content: str
    chunk_seq: Optional[int] = None


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


def _clear_job_files(job_id: int, db: Database, job_files: JobFileStore) -> None:
    """Reset chunk-sequence state and clear pushed files/artifacts for a job."""
    db.reset_job_file_chunk_sequences(job_id)
    job_files.clear(job_id)


def _parse_task_spec(spec: str) -> dict:
    try:
        parsed = json.loads(spec)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


_UPLOAD_CHUNK_SIZE = 1024 * 1024  # 1 MiB


async def _read_upload_capped(file: UploadFile, max_bytes: int) -> bytes:
    """Read an UploadFile in chunks, rejecting bodies over ``max_bytes``."""
    parts = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413, detail="Task upload exceeds size limit"
            )
        parts.append(chunk)
    return b"".join(parts)


def _task_def_fields(record: TaskDefRecord) -> dict:
    """Serialize a TaskDefRecord into response fields."""
    return {
        "id": record.id,
        "name": record.name,
        "desc": record.desc,
        "sha256": record.sha256,
        "created_at": record.created_at.isoformat(),
        "updated_at": (record.updated_at.isoformat() if record.updated_at else None),
        "spec": _parse_task_spec(record.spec),
    }


def create_app(config: Config, db_path: str) -> FastAPI:
    """Create FastAPI application"""

    # Initialize database
    db = Database(db_path)
    db.init_db(config.workspace_path)

    # Create scheduler
    scheduler = TaskScheduler(config, db)
    metrics_store = RunnerMetricsStore(max_age=timedelta(hours=24))
    task_store = TaskStore(Path(config.workspace_path) / "tasks")
    job_files = JobFileStore(
        Path(config.workspace_path),
        config.file_push_max_mb * 1024 * 1024,
        config.artifact_max_mb * 1024 * 1024,
    )

    # Get WebSocket manager
    ws_manager = get_manager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        config.ensure_workspace_structure()
        # Recover orphaned jobs from previous server instance
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

    def require_job_lease(
        job_id: int,
        runner_id: str,
        lease_token: str,
        *,
        request_id: str,
        sync_seq: Optional[int] = None,
        path: Optional[str] = None,
        chunk_seq: Optional[int] = None,
    ) -> JobRecord:
        job = db.get_job(job_id)
        if job is None:
            if path is not None:
                logger.warning(
                    "runner job not found for file ingest",
                    extra={
                        "request_id": request_id,
                        "runner_id": runner_id,
                        "job_id": job_id,
                        "path": path,
                        "chunk_seq": chunk_seq,
                    },
                )
            else:
                logger.warning(
                    "runner job not found for sync ingest",
                    extra={
                        "request_id": request_id,
                        "runner_id": runner_id,
                        "job_id": job_id,
                        "sync_seq": sync_seq,
                    },
                )
            raise HTTPException(status_code=404, detail="Job not found")

        if (
            job.runner_id != runner_id
            or job.lease_token is None
            or lease_token != job.lease_token
        ):
            logger.warning(
                "lease token mismatch",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "job_id": job_id,
                    "sync_seq": sync_seq,
                    "path": path,
                    "chunk_seq": chunk_seq,
                },
            )
            raise HTTPException(status_code=409, detail="Lease token mismatch")

        return job

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

    @app.post("/api/jobs", response_model=JobResponse)
    async def create_job(request: CreateJobRequest):
        """Create a new job"""
        task_def = db.get_task_definition(request.task_id)
        if task_def is None:
            raise HTTPException(status_code=404, detail="Task not found")

        crates_api = CratesAPI(
            rate_limiter=_crates_api_rate_limiter,
            cache=_crates_api_cache,
        )

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

            # Create job in database, snapshotting the task definition
            job_id = db.create_job(
                crate_name=request.crate_name,
                version=version,
                task_id=task_def.id,
                task_name=task_def.name,
                task_sha256=task_def.sha256,
                task_spec=task_def.spec,
            )

            created_job = db.get_job(job_id)
            if created_job is not None:
                payload = _job_to_dict(created_job)
                payload["type"] = "job_created"
                await ws_manager.broadcast_dashboard_update(payload)

            return JobResponse(
                job_id=job_id,
                crate_name=request.crate_name,
                version=version,
                status=TaskStatus.PENDING.value,
            )

        except CrateNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            await crates_api.close()

    # ------------------------------------------------------------------
    # Task definitions
    # ------------------------------------------------------------------

    async def _ingest_task_upload(file: UploadFile):
        """Read a task zip upload and validate it; returns (spec, sha256, path)."""
        zip_bytes = await _read_upload_capped(
            file, config.task_upload_max_mb * 1024 * 1024
        )
        try:
            return task_store.ingest_zip(zip_bytes)
        except ZipValidationError as e:
            logger.warning(
                "task zip rejected",
                extra={"reason": str(e)},
            )
            raise HTTPException(status_code=422, detail=str(e))

    @app.post(
        "/api/tasks",
        response_model=TaskUploadResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def upload_task(file: UploadFile = File(...)):
        """Upload a task definition zip; same sha256 returns the existing task."""
        spec, sha256, zip_path = await _ingest_task_upload(file)

        existing = db.get_task_definition_by_sha256(sha256)
        if existing is not None:
            logger.info(
                "task upload matched existing definition",
                extra={"task_id": existing.id, "sha256": sha256},
            )
            return TaskUploadResponse(**_task_def_fields(existing), existed=True)

        spec_json = json.dumps(spec_to_dict(spec), ensure_ascii=False)
        task_id = db.create_task_definition(
            name=spec.name,
            desc=spec.desc,
            sha256=sha256,
            zip_path=str(zip_path),
            spec=spec_json,
        )
        record = db.get_task_definition(task_id)
        logger.info(
            "task definition created",
            extra={"task_id": task_id, "sha256": sha256, "name": spec.name},
        )
        return TaskUploadResponse(**_task_def_fields(record), existed=False)

    @app.get("/api/tasks", response_model=List[TaskDefinitionResponse])
    async def list_tasks():
        """List all task definitions, newest first."""
        return [
            TaskDefinitionResponse(**_task_def_fields(record))
            for record in db.list_task_definitions()
        ]

    @app.get("/api/tasks/example")
    async def download_example_task():
        """Download an example task zip demonstrating the task structure."""
        return Response(
            content=build_example_task_zip(),
            media_type="application/zip",
            headers={"Content-Disposition": 'attachment; filename="example-task.zip"'},
        )

    @app.get("/api/tasks/{task_id}", response_model=TaskDefinitionResponse)
    async def get_task(task_id: int):
        """Get a task definition by ID."""
        record = db.get_task_definition(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        return TaskDefinitionResponse(**_task_def_fields(record))

    def _task_zip_file(task_id: int) -> tuple[TaskDefRecord, Path]:
        record = db.get_task_definition(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        zip_path = task_store.zip_path(record.sha256)
        if not zip_path.is_file():
            logger.error(
                "task zip missing on disk",
                extra={"task_id": task_id, "sha256": record.sha256},
            )
            raise HTTPException(status_code=404, detail="Task zip not found")
        return record, zip_path

    @app.get("/api/tasks/{task_id}/zip")
    async def download_task_definition_zip(task_id: int):
        """Download the stored zip for a task definition."""
        _, zip_path = _task_zip_file(task_id)
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=f"task-{task_id}.zip",
        )

    @app.get("/api/tasks/{task_id}/task-toml", response_class=PlainTextResponse)
    async def get_task_definition_toml(task_id: int):
        """Return the raw task.toml text from a task definition's zip."""
        _, zip_path = _task_zip_file(task_id)
        try:
            return TaskStore.read_task_toml(zip_path)
        except (zipfile.BadZipFile, KeyError) as exc:
            logger.error(
                "task zip unreadable",
                extra={"task_id": task_id},
            )
            raise HTTPException(status_code=500, detail="Task zip unreadable") from exc

    @app.put(
        "/api/tasks/{task_id}",
        response_model=TaskDefinitionResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def update_task(task_id: int, file: UploadFile = File(...)):
        """Replace a task definition's zip; updates sha256/spec/name/desc."""
        record = db.get_task_definition(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")

        spec, sha256, zip_path = await _ingest_task_upload(file)

        if sha256 == record.sha256:
            logger.info(
                "task update matches current definition; unchanged",
                extra={"task_id": task_id, "sha256": sha256},
            )
            return TaskDefinitionResponse(**_task_def_fields(record))

        conflict = db.get_task_definition_by_sha256(sha256)
        if conflict is not None and conflict.id != task_id:
            logger.warning(
                "task update rejected: sha256 belongs to another task",
                extra={
                    "task_id": task_id,
                    "conflict_task_id": conflict.id,
                    "sha256": sha256,
                },
            )
            raise HTTPException(
                status_code=409,
                detail="Another task already has this zip content",
            )

        spec_json = json.dumps(spec_to_dict(spec), ensure_ascii=False)
        updated = db.update_task_definition(
            task_id,
            name=spec.name,
            desc=spec.desc,
            sha256=sha256,
            zip_path=str(zip_path),
            spec=spec_json,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Task not found")
        task_store.delete_zip(record.sha256)
        logger.info(
            "task definition updated",
            extra={
                "task_id": task_id,
                "old_sha256": record.sha256,
                "new_sha256": sha256,
            },
        )
        return TaskDefinitionResponse(
            **_task_def_fields(db.get_task_definition(task_id))
        )

    @app.delete(
        "/api/tasks/{task_id}",
        response_model=TaskDefinitionResponse,
        dependencies=[Depends(require_admin_token)],
    )
    async def delete_task(task_id: int):
        """Delete a task definition and its stored zip."""
        record = db.delete_task_definition(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")
        task_store.delete_zip(record.sha256)
        logger.info(
            "task definition deleted",
            extra={"task_id": task_id, "sha256": record.sha256},
        )
        return TaskDefinitionResponse(**_task_def_fields(record))

    @app.get("/api/jobs", response_model=List[JobDetailResponse])
    async def get_all_jobs():
        """Get all jobs"""
        jobs = db.get_all_jobs()
        return [_job_to_response(job) for job in jobs]

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
        db.extend_runner_job_leases(runner_id, config.lease_ttl_seconds)
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
        response_model=ClaimJobResponse,
        responses={204: {"description": "No pending jobs"}},
    )
    async def claim_task(
        runner_id: str,
        request: ClaimJobRequest,
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

        job = db.claim_pending_job(runner_id, config.lease_ttl_seconds)
        if job is None:
            return PlainTextResponse(status_code=204, content="")

        _clear_job_files(job.id, db, job_files)

        return ClaimJobResponse(
            job_id=job.id,
            crate_name=job.crate_name,
            version=job.version,
            status=job.status.value,
            runner_id=job.runner_id,
            lease_token=job.lease_token or "",
            lease_expires_at=(
                job.lease_expires_at.isoformat() if job.lease_expires_at else None
            ),
            task_id=job.task_id,
            task_sha256=job.task_sha256,
        )

    @app.post("/api/runners/{runner_id}/jobs/{job_id}/sync")
    async def ingest_runner_job_sync(
        runner_id: str,
        job_id: int,
        request: RunnerTaskSyncRequest,
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)
        require_job_lease(
            job_id,
            runner_id,
            request.lease_token,
            request_id=request_id,
            sync_seq=request.sync_seq,
        )
        applied = db.apply_job_sync(
            job_id,
            request.sync_seq,
            request.status,
            exit_code=request.exit_code,
            message=request.message,
        )
        if applied is None:
            logger.warning(
                "runner job not found for sync ingest",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "job_id": job_id,
                    "sync_seq": request.sync_seq,
                },
            )
            raise HTTPException(status_code=404, detail="Job not found")

        if not applied:
            logger.info(
                "runner job sync not applied",
                extra={
                    "request_id": request_id,
                    "runner_id": runner_id,
                    "job_id": job_id,
                    "sync_seq": request.sync_seq,
                },
            )

        job = db.get_job(job_id)
        last_sync_seq = job.last_sync_seq if job is not None else 0

        if applied:
            updated_job = db.get_job(job_id)
            if updated_job is not None:
                job_payload = _job_to_dict(updated_job)
                job_payload["type"] = "job_update"
                await ws_manager.broadcast_job_update(job_id, job_payload)

                dashboard_payload = _job_to_dict(updated_job)
                dashboard_payload["type"] = (
                    "job_completed"
                    if TaskStatus(request.status)
                    in (
                        TaskStatus.COMPLETED,
                        TaskStatus.FAILED,
                        TaskStatus.CANCELLED,
                        TaskStatus.TIMEOUT,
                        TaskStatus.OOM,
                        TaskStatus.RUNNER_FAILED,
                    )
                    else "job_update"
                )
                await ws_manager.broadcast_dashboard_update(dashboard_payload)

        return {"synced": applied, "last_sync_seq": last_sync_seq}

    @app.get("/api/runners/{runner_id}/tasks/{task_id}/zip")
    async def download_runner_task_zip(
        runner_id: str,
        task_id: int,
        _auth: None = Depends(require_runner_auth),
    ):
        record = db.get_task_definition(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Task not found")

        zip_path = task_store.zip_path(record.sha256)
        if not zip_path.is_file():
            logger.warning(
                "task zip missing on disk",
                extra={"task_id": task_id, "sha256": record.sha256},
            )
            raise HTTPException(status_code=404, detail="Task zip not found")

        return FileResponse(zip_path, media_type="application/zip")

    @app.post("/api/runners/{runner_id}/jobs/{job_id}/files")
    async def push_runner_job_file(
        runner_id: str,
        job_id: int,
        request: RunnerFilePushRequest,
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)

        require_job_lease(
            job_id,
            runner_id,
            request.lease_token,
            request_id=request_id,
            path=request.path,
            chunk_seq=request.chunk_seq,
        )

        if request.mode == "append" and request.chunk_seq is None:
            raise HTTPException(
                status_code=422, detail="chunk_seq is required for append mode"
            )

        try:
            # Path validation intentionally runs BEFORE record_job_file_chunk:
            # record_job_file_chunk consumes chunk_seq, so a path rejected
            # after consumption would burn the sequence number and cause the
            # runner's retry of that chunk to be silently dropped.
            JobFileStore.safe_join(job_files.files_dir(job_id), request.path)
        except PathSafetyError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if request.mode == "append":
            should_apply = db.record_job_file_chunk(
                job_id, request.path, request.chunk_seq
            )
            if should_apply:
                job_files.append_file(job_id, request.path, request.content)
            return {"applied": should_apply}

        job_files.write_full(job_id, request.path, request.content)
        return {"applied": True}

    @app.post("/api/runners/{runner_id}/jobs/{job_id}/artifacts")
    async def upload_runner_job_artifact(
        runner_id: str,
        job_id: int,
        lease_token: str = Form(...),
        path: str = Form(...),
        file: UploadFile = File(...),
        x_request_id: Optional[str] = Header(default=None, alias="X-Request-ID"),
        _auth: None = Depends(require_runner_auth),
    ):
        request_id = _request_id_from_header(x_request_id)

        require_job_lease(
            job_id,
            runner_id,
            lease_token,
            request_id=request_id,
            path=path,
        )

        try:
            size = job_files.save_artifact(job_id, path, file.file)
        except PathSafetyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ArtifactTooLargeError as e:
            raise HTTPException(status_code=413, detail=str(e))

        logger.info(
            "artifact uploaded",
            extra={
                "request_id": request_id,
                "runner_id": runner_id,
                "job_id": job_id,
                "path": path,
                "size_bytes": size,
            },
        )
        return {"saved": True, "size": size}

    @app.get("/api/jobs/{job_id}", response_model=JobDetailResponse)
    async def get_job(job_id: int):
        """Get job by ID"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return _job_to_response(job)

    @app.post("/api/jobs/{job_id}/cancel")
    async def cancel_running_job(job_id: int):
        """Cancel a running job (does not delete from database)"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status != TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Job is not running")

        await scheduler.cancel_task(job_id)
        return {"message": "Job cancelled"}

    @app.post("/api/jobs/{job_id}/retry", response_model=JobResponse)
    async def retry_job(job_id: int):
        """Retry/re-execute a job by resetting it to pending state"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        # Cannot retry a job that's currently running
        if job.status == TaskStatus.RUNNING:
            raise HTTPException(status_code=400, detail="Cannot retry a running job")

        # The backing task definition must still exist
        if db.get_task_definition(job.task_id) is None:
            raise HTTPException(status_code=400, detail="Task has been deleted")

        # Reset job to pending state and clear files/artifacts from prior attempts
        db.reset_job_for_retry(job_id)
        _clear_job_files(job_id, db, job_files)

        return JobResponse(
            job_id=job_id,
            crate_name=job.crate_name,
            version=job.version,
            status=TaskStatus.PENDING.value,
        )

    @app.delete("/api/jobs/{job_id}")
    async def delete_job(job_id: int):
        """Delete a job from database (cannot delete running jobs)"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        if job.status == TaskStatus.RUNNING:
            raise HTTPException(
                status_code=400, detail="Cannot delete running job. Cancel it first."
            )

        deleted = db.delete_job(job_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Job not found")

        return {"message": "Job deleted"}

    @app.post("/api/jobs/batch-retry")
    async def batch_retry_jobs(request: BatchJobRequest):
        """Batch retry multiple jobs"""
        results = {"retried": [], "skipped": [], "not_found": []}

        for job_id in request.job_ids:
            job = db.get_job(job_id)
            if not job:
                results["not_found"].append(job_id)
            elif job.status == TaskStatus.RUNNING:
                results["skipped"].append(job_id)
            elif db.get_task_definition(job.task_id) is None:
                # Same precondition as single-job retry (400 "Task has been
                # deleted"): skip instead of resetting the job.
                results["skipped"].append(job_id)
            else:
                db.reset_job_for_retry(job_id)
                _clear_job_files(job_id, db, job_files)
                results["retried"].append(job_id)

        return results

    @app.post("/api/jobs/batch-delete")
    async def batch_delete_jobs(request: BatchJobRequest):
        """Batch delete multiple jobs"""
        results = {"deleted": [], "skipped": [], "not_found": []}

        for job_id in request.job_ids:
            job = db.get_job(job_id)
            if not job:
                results["not_found"].append(job_id)
            elif job.status == TaskStatus.RUNNING:
                results["skipped"].append(job_id)
            else:
                db.delete_job(job_id)
                results["deleted"].append(job_id)

        return results

    @app.post("/api/jobs/batch-priority")
    async def batch_set_priority(request: BatchPriorityRequest):
        """Batch set priority on pending jobs. Skips non-pending jobs."""
        results = {"updated": [], "skipped": [], "not_found": []}
        for job_id in request.job_ids:
            job = db.get_job(job_id)
            if not job:
                results["not_found"].append(job_id)
            elif job.status != TaskStatus.PENDING:
                results["skipped"].append(job_id)
            else:
                db.update_job_priority(job_id, request.priority)
                results["updated"].append(job_id)
        return results

    @app.post("/api/jobs/batch-cancel")
    async def batch_cancel_jobs(request: BatchJobRequest):
        """Batch cancel running jobs."""
        results = {"cancelled": [], "skipped": [], "not_found": []}
        for job_id in request.job_ids:
            job = db.get_job(job_id)
            if not job:
                results["not_found"].append(job_id)
            elif job.status != TaskStatus.RUNNING:
                results["skipped"].append(job_id)
            else:
                await scheduler.cancel_task(job_id)
                results["cancelled"].append(job_id)
        return results

    @app.get("/api/queue")
    async def get_queue():
        """Get queue state."""
        running = db.get_jobs_by_status(TaskStatus.RUNNING)
        pending = db.get_pending_jobs_ordered()
        return {
            "running": [_job_to_dict(j) for j in running],
            "pending": [_job_to_dict(j) for j in pending],
        }

    @app.get("/api/dashboard/stats")
    async def get_dashboard_stats():
        """Get job statistics for dashboard"""
        all_jobs = db.get_all_jobs()

        stats = {
            "total": len(all_jobs),
            "pending": len([j for j in all_jobs if j.status == TaskStatus.PENDING]),
            "running": len([j for j in all_jobs if j.status == TaskStatus.RUNNING]),
            "completed": len([j for j in all_jobs if j.status == TaskStatus.COMPLETED]),
            "failed": len([j for j in all_jobs if j.status == TaskStatus.FAILED]),
            "cancelled": len([j for j in all_jobs if j.status == TaskStatus.CANCELLED]),
            "timeout": len([j for j in all_jobs if j.status == TaskStatus.TIMEOUT]),
            "oom": len([j for j in all_jobs if j.status == TaskStatus.OOM]),
            "runner_failed": len(
                [j for j in all_jobs if j.status == TaskStatus.RUNNER_FAILED]
            ),
        }

        return stats

    @app.get("/api/dashboard/system")
    async def get_system_stats():
        """Get system resource statistics"""
        monitor = SystemMonitor()
        return monitor.get_system_stats()

    @app.get("/api/jobs/{job_id}/files")
    async def list_job_files(job_id: int):
        """List files pushed by the runner for a job"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_files.list_files(job_id)

    @app.get("/api/jobs/{job_id}/files/{path:path}")
    async def get_job_file(
        job_id: int,
        path: str,
        lines: int = Query(default=1000, ge=0),
        raw: bool = Query(default=False),
    ):
        """Read a pushed file: tail N lines as JSON, or full text with raw=true"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        try:
            if raw:
                return PlainTextResponse(job_files.read_raw(job_id, path))
            return {"lines": job_files.read_tail(job_id, path, lines)}
        except PathSafetyError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except FileNotFoundError:
            # JobFileStore pre-checks existence and raises the builtin.
            raise HTTPException(status_code=404, detail="File not found")
        except CustomFileNotFoundError as e:
            # read_last_n_lines wraps mid-read I/O failures in its custom
            # FileNotFoundError; treat those as server-side read errors.
            logger.error(
                "failed to read job file",
                extra={"job_id": job_id, "path": path, "error": str(e)},
            )
            raise HTTPException(status_code=500, detail="Failed to read file")

    @app.get("/api/jobs/{job_id}/artifacts")
    async def list_job_artifacts(job_id: int):
        """List artifacts uploaded for a job"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job_files.list_artifacts(job_id)

    @app.get("/api/jobs/{job_id}/artifacts/{path:path}")
    async def download_job_artifact(job_id: int, path: str):
        """Download an uploaded artifact"""
        job = db.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        try:
            artifact_path = job_files.artifact_file_path(job_id, path)
        except PathSafetyError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not artifact_path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")

        return FileResponse(artifact_path)

    @app.websocket("/ws/jobs/{job_id}")
    async def websocket_job_endpoint(websocket: WebSocket, job_id: int):
        """WebSocket endpoint for real-time job status updates"""
        # Check if job exists
        job = db.get_job(job_id)
        if not job:
            await websocket.close(code=1000)
            return

        # Connect and send initial state
        await ws_manager.connect_job(job_id, websocket)

        try:
            # Send initial job state
            await websocket.send_json(_job_to_dict(job))

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
            ws_manager.disconnect_job(job_id, websocket)

    @app.websocket("/ws/dashboard")
    async def websocket_dashboard_endpoint(websocket: WebSocket):
        """WebSocket endpoint for real-time dashboard updates"""
        await ws_manager.connect_dashboard(websocket)

        try:
            # Send initial dashboard stats
            all_jobs = db.get_all_jobs()
            stats = {
                "total": len(all_jobs),
                "pending": len([j for j in all_jobs if j.status == TaskStatus.PENDING]),
                "running": len([j for j in all_jobs if j.status == TaskStatus.RUNNING]),
                "completed": len(
                    [j for j in all_jobs if j.status == TaskStatus.COMPLETED]
                ),
                "failed": len([j for j in all_jobs if j.status == TaskStatus.FAILED]),
                "cancelled": len(
                    [j for j in all_jobs if j.status == TaskStatus.CANCELLED]
                ),
                "timeout": len([j for j in all_jobs if j.status == TaskStatus.TIMEOUT]),
                "oom": len([j for j in all_jobs if j.status == TaskStatus.OOM]),
                "runner_failed": len(
                    [j for j in all_jobs if j.status == TaskStatus.RUNNER_FAILED]
                ),
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


def _job_to_dict(job: JobRecord) -> dict:
    """Convert JobRecord to dictionary for WebSocket"""
    return {
        "id": job.id,
        "crate_name": job.crate_name,
        "version": job.version,
        "status": job.status.value,
        "exit_code": job.exit_code,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "error_message": job.error_message,
        "message": job.message,
        "priority": job.priority,
        "runner_id": job.runner_id,
        "task_id": job.task_id,
        "task_name": job.task_name,
        "task_sha256": job.task_sha256,
        "task_spec": _parse_task_spec(job.task_spec),
    }


def _job_to_response(job: JobRecord) -> JobDetailResponse:
    """Convert JobRecord to response model"""
    return JobDetailResponse(
        id=job.id,
        crate_name=job.crate_name,
        version=job.version,
        status=job.status.value,
        exit_code=job.exit_code,
        created_at=job.created_at.isoformat() if job.created_at else None,
        started_at=job.started_at.isoformat() if job.started_at else None,
        finished_at=job.finished_at.isoformat() if job.finished_at else None,
        error_message=job.error_message,
        message=job.message,
        priority=job.priority,
        runner_id=job.runner_id,
        task_id=job.task_id,
        task_name=job.task_name,
        task_sha256=job.task_sha256,
        task_spec=_parse_task_spec(job.task_spec),
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
