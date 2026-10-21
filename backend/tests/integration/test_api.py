import pytest
from fastapi.testclient import TestClient
from pathlib import Path
from app.main import create_app
from app.config import Config
from app.database import Database


@pytest.fixture
def config(tmp_path):
    cfg = Config(workspace_path=tmp_path / "workspace")
    cfg.ensure_workspace_structure()
    return cfg


@pytest.fixture
def app(config, tmp_path):
    db_path = config.get_db_full_path()
    return create_app(config, str(db_path))


@pytest.fixture
def client(app):
    return TestClient(app)


@pytest.fixture(autouse=True)
def mock_crates_api(monkeypatch):
    """Avoid real crates.io calls in tests."""

    class MockCratesAPI:
        async def get_latest_version(self, _crate_name):
            return "1.0.0"

        async def verify_version_exists(self, _crate_name, _version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda **kwargs: MockCratesAPI())


def create_task_definition(app, name="cargo-rapx"):
    db = app.state.scheduler.db
    return db.create_task_definition(
        name=name,
        desc="test task",
        sha256=f"sha256-{name}",
        zip_path=f"/tmp/{name}.zip",
        spec='{"name": "%s"}' % name,
    )


def create_job(
    client, app, crate_name="serde", version="1.0.0", task_name="cargo-rapx"
):
    task_id = create_task_definition(app, name=task_name)
    response = client.post(
        "/api/jobs",
        json={"crate_name": crate_name, "version": version, "task_id": task_id},
    )
    assert response.status_code == 200
    return response.json()["job_id"]


def job_log_file(config, job_id, log_name):
    path = config.workspace_path / "jobs" / str(job_id) / "files" / f"{log_name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_root_redirect(client):
    """Test root redirects to docs"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307


def test_create_job_with_version(client, app):
    """Test creating a job with explicit version"""
    task_id = create_task_definition(app)
    response = client.post(
        "/api/jobs",
        json={"crate_name": "serde", "version": "1.0.0", "task_id": task_id},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["job_id"] == 1
    assert data["crate_name"] == "serde"
    assert data["version"] == "1.0.0"
    assert data["status"] == "pending"


def test_create_job_without_version_resolves_latest(client, app):
    """Version omitted -> resolved via crates.io"""
    task_id = create_task_definition(app)
    response = client.post(
        "/api/jobs", json={"crate_name": "serde", "task_id": task_id}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == "1.0.0"
    assert data["status"] == "pending"


def test_create_job_unknown_task_returns_404(client):
    response = client.post(
        "/api/jobs",
        json={"crate_name": "serde", "version": "1.0.0", "task_id": 4242},
    )
    assert response.status_code == 404


def test_create_job_does_not_dedup_crate_version(client, app):
    """Same crate+version always creates a fresh job"""
    task_id = create_task_definition(app)
    first = client.post(
        "/api/jobs",
        json={"crate_name": "serde", "version": "1.0.0", "task_id": task_id},
    )
    second = client.post(
        "/api/jobs",
        json={"crate_name": "serde", "version": "1.0.0", "task_id": task_id},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["job_id"] != second.json()["job_id"]


def test_get_all_jobs(client, app):
    """Test retrieving all jobs"""
    create_job(client, app)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    jobs = response.json()
    assert len(jobs) == 1
    assert jobs[0]["crate_name"] == "serde"
    assert "runner_id" in jobs[0]
    assert jobs[0]["task_name"] == "cargo-rapx"
    assert "case_count" not in jobs[0]
    assert "poc_count" not in jobs[0]
    assert "compile_failed" not in jobs[0]


def test_get_job_by_id(client, app):
    """Test retrieving specific job"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == job_id
    assert data["crate_name"] == "serde"
    assert "runner_id" in data
    assert data["task_spec"] == {"name": "cargo-rapx"}
    assert data["task_sha256"] == "sha256-cargo-rapx"
    assert "case_count" not in data
    assert "poc_count" not in data
    assert "compile_failed" not in data


def test_queue_items_include_runner_id(client, app):
    create_job(client, app)

    response = client.get("/api/queue")

    assert response.status_code == 200
    data = response.json()
    assert "running" in data
    assert "pending" in data
    if data["pending"]:
        assert "runner_id" in data["pending"][0]


def test_delete_job_not_running(client, app):
    """Test deleting non-running job succeeds"""
    job_id = create_job(client, app)

    response = client.delete(f"/api/jobs/{job_id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Job deleted"

    # Verify job is actually deleted
    get_response = client.get(f"/api/jobs/{job_id}")
    assert get_response.status_code == 404


def test_dashboard_stats(client, app):
    """Test getting dashboard statistics"""
    # Create jobs via API
    create_job(client, app, crate_name="serde", task_name="task-a")
    create_job(client, app, crate_name="tokio", task_name="task-b")

    response = client.get("/api/dashboard/stats")

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "pending" in data
    assert "running" in data
    assert "completed" in data
    assert "failed" in data
    assert isinstance(data["total"], int)
    assert data["total"] >= 0


def test_dashboard_system(client):
    """Test getting system resource stats"""
    response = client.get("/api/dashboard/system")

    assert response.status_code == 200
    data = response.json()

    # Verify flat structure
    assert "cpu_percent" in data
    assert "memory_percent" in data
    assert "memory_used_gb" in data
    assert "memory_total_gb" in data
    assert "disk_percent" in data
    assert "disk_used_gb" in data
    assert "disk_total_gb" in data

    assert isinstance(data["cpu_percent"], float)
    assert isinstance(data["memory_percent"], float)
    assert isinstance(data["disk_percent"], float)
    assert 0.0 <= data["cpu_percent"] <= 100.0


def test_get_job_logs(client, app, config):
    """Test getting stdout logs for a job"""
    job_id = create_job(client, app)

    # Create a fake log file under jobs/{id}/files/
    log_file = job_log_file(config, job_id, "stdout")
    log_file.write_text("\n".join([f"Log line {i}" for i in range(1, 21)]))

    # Get last 10 lines
    response = client.get(f"/api/jobs/{job_id}/logs/stdout?lines=10")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert len(data["lines"]) == 10
    assert data["lines"][0] == "Log line 11"
    assert data["lines"][-1] == "Log line 20"


def test_get_job_logs_not_found(client):
    """Test getting logs for non-existent job"""
    response = client.get("/api/jobs/9999/logs/stdout")

    assert response.status_code == 404


def test_get_job_logs_file_missing(client, app):
    """Test getting logs when file doesn't exist"""
    job_id = create_job(client, app)

    # Don't create the log file - it should be missing
    response = client.get(f"/api/jobs/{job_id}/logs/stdout")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_raw(client, app, config):
    """Test downloading full log"""
    job_id = create_job(client, app)

    log_file = job_log_file(config, job_id, "stdout")
    log_content = "Full log content\nLine 2\nLine 3"
    log_file.write_text(log_content)

    response = client.get(f"/api/jobs/{job_id}/logs/stdout/raw")

    assert response.status_code == 200


def test_retry_job(client, app):
    """Test retrying a job resets it to pending state"""
    job_id = create_job(client, app)

    retry_resp = client.post(f"/api/jobs/{job_id}/retry")
    assert retry_resp.status_code == 200
    retry_data = retry_resp.json()

    # Verify same job ID is returned
    assert retry_data["job_id"] == job_id
    assert retry_data["status"] == "pending"
    assert retry_data["crate_name"] == "serde"
    assert retry_data["version"] == "1.0.0"


def test_retry_nonexistent_job(client):
    """Test retrying a non-existent job returns 404"""
    response = client.post("/api/jobs/99999/retry")
    assert response.status_code == 404


def test_retry_job_with_deleted_task_returns_400(client, app):
    """Retrying a job whose task definition was deleted returns 400"""
    job_id = create_job(client, app)
    db = app.state.scheduler.db
    job = db.get_job(job_id)
    db.delete_task_definition(job.task_id)

    response = client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 400


def test_retry_clears_job_files_and_artifacts(client, app, config):
    """Retry removes jobs/{id}/files and jobs/{id}/artifacts directories"""
    job_id = create_job(client, app)

    files_dir = config.workspace_path / "jobs" / str(job_id) / "files"
    artifacts_dir = config.workspace_path / "jobs" / str(job_id) / "artifacts"
    files_dir.mkdir(parents=True)
    artifacts_dir.mkdir(parents=True)
    (files_dir / "stdout.log").write_text("old log\n")
    (artifacts_dir / "report.html").write_text("old artifact\n")

    response = client.post(f"/api/jobs/{job_id}/retry")
    assert response.status_code == 200

    assert not files_dir.exists()
    assert not artifacts_dir.exists()


def test_generic_log_endpoint(client, app, config):
    """GET /api/jobs/{id}/logs/stdout returns last N lines"""
    job_id = create_job(client, app)

    log_file = job_log_file(config, job_id, "stdout")
    log_file.write_text("\n".join([f"line {i}" for i in range(1, 6)]))

    response = client.get(f"/api/jobs/{job_id}/logs/stdout?lines=3")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert len(data["lines"]) == 3
    assert data["lines"][-1] == "line 5"


def test_generic_log_endpoint_unknown_name(client, app):
    """GET /api/jobs/{id}/logs/{unknown} returns 404 Log file not found"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/logs/unknown_log_type")

    assert response.status_code == 404
    assert "Log file not found" in response.json()["detail"]


def test_generic_log_raw_unknown_name(client, app):
    """GET /api/jobs/{id}/logs/{unknown}/raw returns 404 Log file not found"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/logs/unknown_log_type/raw")

    assert response.status_code == 404
    assert "Log file not found" in response.json()["detail"]


def test_generic_log_raw_endpoint(client, app, config):
    """GET /api/jobs/{id}/logs/{name}/raw returns full plain text content"""
    job_id = create_job(client, app)

    log_file = job_log_file(config, job_id, "stdout")
    log_content = "Full content line 1\nFull content line 2"
    log_file.write_text(log_content)

    response = client.get(f"/api/jobs/{job_id}/logs/stdout/raw")

    assert response.status_code == 200
    assert log_content in response.text


def test_generic_log_endpoint_file_missing(client, app):
    """GET /api/jobs/{id}/logs/{name} returns 404 when file doesn't exist"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/logs/stdout")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_batch_retry_uses_job_ids_key(client, app):
    """POST /api/jobs/batch-retry accepts job_ids body key"""
    job_id = create_job(client, app)

    response = client.post("/api/jobs/batch-retry", json={"job_ids": [job_id, 99999]})

    assert response.status_code == 200
    data = response.json()
    assert data["retried"] == [job_id]
    assert data["not_found"] == [99999]


def test_batch_delete_uses_job_ids_key(client, app):
    """POST /api/jobs/batch-delete accepts job_ids body key"""
    job_id = create_job(client, app)

    response = client.post("/api/jobs/batch-delete", json={"job_ids": [job_id]})

    assert response.status_code == 200
    assert response.json()["deleted"] == [job_id]
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_batch_priority_uses_job_ids_key(client, app):
    """POST /api/jobs/batch-priority accepts job_ids body key"""
    job_id = create_job(client, app)

    response = client.post(
        "/api/jobs/batch-priority", json={"job_ids": [job_id], "priority": 5}
    )

    assert response.status_code == 200
    assert response.json()["updated"] == [job_id]
    assert client.get(f"/api/jobs/{job_id}").json()["priority"] == 5
