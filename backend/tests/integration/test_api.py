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


def job_file(config, job_id, rel_path):
    path = config.workspace_path / "jobs" / str(job_id) / "files" / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def artifact_file(config, job_id, rel_path):
    path = config.workspace_path / "jobs" / str(job_id) / "artifacts" / rel_path
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
    """Test retrieving jobs (paginated envelope)"""
    create_job(client, app)

    response = client.get("/api/jobs")

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 20
    jobs = payload["items"]
    assert len(jobs) == 1
    assert jobs[0]["crate_name"] == "serde"
    assert "runner_id" in jobs[0]
    assert jobs[0]["task_name"] == "cargo-rapx"
    assert "case_count" not in jobs[0]
    assert "poc_count" not in jobs[0]
    assert "compile_failed" not in jobs[0]


def _create_jobs_for_listing(client, app, total: int):
    """Create one task definition and `total` jobs against it."""
    task_id = create_task_definition(app)
    for index in range(total):
        crate = "serde" if index % 2 == 0 else "tokio"
        response = client.post(
            "/api/jobs",
            json={
                "crate_name": crate,
                "version": f"1.0.{index}",
                "task_id": task_id,
            },
        )
        assert response.status_code == 200
    return task_id


def test_list_jobs_pagination(client, app):
    _create_jobs_for_listing(client, app, 25)

    first = client.get("/api/jobs", params={"page": 1, "page_size": 20}).json()
    second = client.get("/api/jobs", params={"page": 2, "page_size": 20}).json()

    assert first["total"] == 25
    assert len(first["items"]) == 20
    assert second["total"] == 25
    assert len(second["items"]) == 5

    first_ids = {job["id"] for job in first["items"]}
    second_ids = {job["id"] for job in second["items"]}
    assert first_ids.isdisjoint(second_ids)


def test_list_jobs_compound_filter(client, app):
    _create_jobs_for_listing(client, app, 10)

    response = client.get(
        "/api/jobs",
        params={"crate_name": "serde", "status": "pending", "page_size": 100},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 5
    assert all(job["crate_name"] == "serde" for job in payload["items"])
    assert all(job["status"] == "pending" for job in payload["items"])


def test_list_jobs_filter_by_task_name(client, app):
    _create_jobs_for_listing(client, app, 3)

    payload = client.get(
        "/api/jobs", params={"task_name": "cargo-rapx", "page_size": 100}
    ).json()

    assert payload["total"] == 3


def test_list_jobs_invalid_status_returns_422(client):
    response = client.get("/api/jobs", params={"status": "bogus"})
    assert response.status_code == 422


def test_list_jobs_sort_by_crate_name(client, app):
    _create_jobs_for_listing(client, app, 4)

    payload = client.get(
        "/api/jobs",
        params={"sort_by": "crate_name", "sort_dir": "asc", "page_size": 100},
    ).json()

    names = [job["crate_name"] for job in payload["items"]]
    assert names == sorted(names)


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


def test_list_job_files(client, app, config):
    """GET /api/jobs/{id}/files lists pushed files with path/size/mtime"""
    job_id = create_job(client, app)

    job_file(config, job_id, "stdout.log").write_text("hello\n")
    job_file(config, job_id, "nested/watch.txt").write_text("watch\n")

    response = client.get(f"/api/jobs/{job_id}/files")

    assert response.status_code == 200
    entries = response.json()
    assert [entry["path"] for entry in entries] == [
        "nested/watch.txt",
        "stdout.log",
    ]
    for entry in entries:
        assert entry["size"] > 0
        assert isinstance(entry["mtime"], (int, float))


def test_list_job_files_empty(client, app):
    """GET /api/jobs/{id}/files returns [] when nothing was pushed"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/files")

    assert response.status_code == 200
    assert response.json() == []


def test_list_job_files_job_not_found(client):
    """GET /api/jobs/{id}/files returns 404 for unknown job"""
    response = client.get("/api/jobs/9999/files")

    assert response.status_code == 404


def test_get_job_file_tail(client, app, config):
    """GET /api/jobs/{id}/files/{path} returns the last N lines by default"""
    job_id = create_job(client, app)

    job_file(config, job_id, "stdout.log").write_text(
        "\n".join([f"Log line {i}" for i in range(1, 21)])
    )

    response = client.get(f"/api/jobs/{job_id}/files/stdout.log?lines=10")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert len(data["lines"]) == 10
    assert data["lines"][0] == "Log line 11"
    assert data["lines"][-1] == "Log line 20"


def test_get_job_file_tail_default_lines(client, app, config):
    """Without ?lines= the whole small file is returned (default tail 1000)"""
    job_id = create_job(client, app)

    job_file(config, job_id, "stdout.log").write_text("a\nb\nc")

    response = client.get(f"/api/jobs/{job_id}/files/stdout.log")

    assert response.status_code == 200
    assert response.json()["lines"] == ["a", "b", "c"]


def test_get_job_file_raw(client, app, config):
    """?raw=true returns the full file as plain text"""
    job_id = create_job(client, app)

    content = "Full log content\nLine 2\nLine 3"
    job_file(config, job_id, "stdout.log").write_text(content)

    response = client.get(f"/api/jobs/{job_id}/files/stdout.log?raw=true")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert content in response.text


def test_get_job_file_nested_path(client, app, config):
    """Nested relative paths are served by the files endpoint"""
    job_id = create_job(client, app)

    job_file(config, job_id, "sub/dir/run.log").write_text("nested content\n")

    response = client.get(f"/api/jobs/{job_id}/files/sub/dir/run.log?raw=true")

    assert response.status_code == 200
    assert "nested content" in response.text


def test_get_job_file_job_not_found(client):
    """GET /api/jobs/{id}/files/{path} returns 404 for unknown job"""
    response = client.get("/api/jobs/9999/files/stdout.log")

    assert response.status_code == 404


def test_get_job_file_not_found(client, app):
    """GET /api/jobs/{id}/files/{path} returns 404 when file is missing"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/files/missing.log")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_job_file_raw_not_found(client, app):
    """?raw=true on a missing file also returns 404"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/files/missing.log?raw=true")

    assert response.status_code == 404


def test_get_job_file_path_traversal(client, app):
    """Path traversal in files endpoint is rejected with 400"""
    job_id = create_job(client, app)

    for url in (
        f"/api/jobs/{job_id}/files/%2e%2e/secret",
        f"/api/jobs/{job_id}/files/%2Fetc%2Fpasswd",
    ):
        response = client.get(url)
        assert response.status_code == 400, url


def test_list_job_artifacts(client, app, config):
    """GET /api/jobs/{id}/artifacts lists artifacts with path/size"""
    job_id = create_job(client, app)

    artifact_file(config, job_id, "report.html").write_text("<html></html>")
    artifact_file(config, job_id, "pocs/poc1.rs").write_text("fn main() {}")

    response = client.get(f"/api/jobs/{job_id}/artifacts")

    assert response.status_code == 200
    entries = response.json()
    assert [entry["path"] for entry in entries] == [
        "pocs/poc1.rs",
        "report.html",
    ]
    for entry in entries:
        assert set(entry.keys()) == {"path", "size"}
        assert entry["size"] > 0


def test_list_job_artifacts_empty(client, app):
    """GET /api/jobs/{id}/artifacts returns [] when nothing was uploaded"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/artifacts")

    assert response.status_code == 200
    assert response.json() == []


def test_list_job_artifacts_job_not_found(client):
    """GET /api/jobs/{id}/artifacts returns 404 for unknown job"""
    response = client.get("/api/jobs/9999/artifacts")

    assert response.status_code == 404


def test_download_job_artifact(client, app, config):
    """GET /api/jobs/{id}/artifacts/{path} downloads the artifact file"""
    job_id = create_job(client, app)

    content = "<html>report</html>"
    artifact_file(config, job_id, "report.html").write_text(content)

    response = client.get(f"/api/jobs/{job_id}/artifacts/report.html")

    assert response.status_code == 200
    assert response.text == content


def test_download_job_artifact_nested(client, app, config):
    """Artifacts under nested directories are downloadable"""
    job_id = create_job(client, app)

    artifact_file(config, job_id, "pocs/poc1.rs").write_text("fn main() {}")

    response = client.get(f"/api/jobs/{job_id}/artifacts/pocs/poc1.rs")

    assert response.status_code == 200
    assert response.text == "fn main() {}"


def test_download_job_artifact_job_not_found(client):
    """Artifact download returns 404 for unknown job"""
    response = client.get("/api/jobs/9999/artifacts/report.html")

    assert response.status_code == 404


def test_download_job_artifact_not_found(client, app):
    """Artifact download returns 404 when file is missing"""
    job_id = create_job(client, app)

    response = client.get(f"/api/jobs/{job_id}/artifacts/missing.bin")

    assert response.status_code == 404


def test_download_job_artifact_path_traversal(client, app):
    """Path traversal in artifact download is rejected with 400"""
    job_id = create_job(client, app)

    for url in (
        f"/api/jobs/{job_id}/artifacts/%2e%2e/secret",
        f"/api/jobs/{job_id}/artifacts/%2Fetc%2Fpasswd",
    ):
        response = client.get(url)
        assert response.status_code == 400, url


def test_old_logs_endpoints_removed(client, app, config):
    """Old /api/jobs/{id}/logs/... endpoints no longer exist"""
    job_id = create_job(client, app)
    job_file(config, job_id, "stdout.log").write_text("line\n")

    assert client.get(f"/api/jobs/{job_id}/logs/stdout").status_code == 404
    assert client.get(f"/api/jobs/{job_id}/logs/stdout/raw").status_code == 404


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


def test_batch_retry_uses_job_ids_key(client, app):
    """POST /api/jobs/batch-retry accepts job_ids body key"""
    job_id = create_job(client, app)

    response = client.post("/api/jobs/batch-retry", json={"job_ids": [job_id, 99999]})

    assert response.status_code == 200
    data = response.json()
    assert data["retried"] == [job_id]
    assert data["not_found"] == [99999]


def test_batch_retry_skips_job_with_deleted_task(client, app):
    """Batch retry skips (not retries) jobs whose task definition was deleted,
    matching the single-retry 400 semantics."""
    job_id = create_job(client, app)
    db = app.state.scheduler.db
    job = db.get_job(job_id)
    db.delete_task_definition(job.task_id)

    response = client.post("/api/jobs/batch-retry", json={"job_ids": [job_id]})

    assert response.status_code == 200
    data = response.json()
    assert data["retried"] == []
    assert data["skipped"] == [job_id]


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


def test_list_jobs_sort_by_runtime(client, app):
    """Runtime sorting uses finished_at - started_at, NULLs last."""
    from datetime import datetime, timedelta

    from core.models import TaskStatus

    task_id = create_task_definition(app)
    db = app.state.scheduler.db
    base = datetime(2026, 1, 1, 12, 0, 0)
    runtimes = [3600, 60, 600]
    ids = []
    for index, runtime in enumerate(runtimes):
        response = client.post(
            "/api/jobs",
            json={
                "crate_name": f"crate{index}",
                "version": "1.0.0",
                "task_id": task_id,
            },
        )
        job_id = response.json()["job_id"]
        ids.append(job_id)
        db.update_job_status(
            job_id,
            TaskStatus.COMPLETED,
            started_at=base,
            finished_at=base + timedelta(seconds=runtime),
        )

    payload = client.get(
        "/api/jobs",
        params={"sort_by": "runtime", "sort_dir": "asc", "page_size": 100},
    ).json()

    assert [job["id"] for job in payload["items"]] == [ids[1], ids[2], ids[0]]
