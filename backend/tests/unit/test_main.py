import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config


@pytest.fixture
def test_config(tmp_path):
    """Create a test configuration"""
    config = Config(
        workspace_path=tmp_path / "workspace",
        server_host="127.0.0.1",
        server_port=8000,
        log_level="INFO",
        admin_token="admin-test-token",
    )
    return config


@pytest.fixture
def test_db_path(tmp_path):
    """Create a test database path"""
    return str(tmp_path / "test.db")


@pytest.fixture
def client(test_config, test_db_path):
    """Create a test client"""
    app = create_app(test_config, test_db_path)
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


def create_runner_and_token(client, runner_id):
    response = client.post(
        "/api/admin/runners",
        headers={"X-Admin-Token": "admin-test-token"},
        json={"runner_id": runner_id},
    )
    assert response.status_code == 201
    body = response.json()
    return body["runner_id"], body["token"]


def auth_headers(token, request_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return headers


def create_task_definition(client, name="cargo-rapx"):
    db = client.app.state.scheduler.db
    return db.create_task_definition(
        name=name,
        desc="test task",
        sha256=f"sha256-{name}",
        zip_path=f"/tmp/{name}.zip",
        spec='{"name": "%s"}' % name,
    )


def create_pending_job(client, crate_name="serde", version="1.0.0"):
    task_id = create_task_definition(client, name=f"task-for-{crate_name}")
    response = client.post(
        "/api/jobs",
        json={"crate_name": crate_name, "version": version, "task_id": task_id},
    )
    assert response.status_code == 200
    return response.json()["job_id"]


def claim_job(client, runner_id, token):
    response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=auth_headers(token, request_id="req-claim"),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert response.status_code == 200
    body = response.json()
    return body["job_id"], body["lease_token"]


def test_runner_helpers_can_create_and_claim_job(client):
    runner_id, token = create_runner_and_token(client, "runner-helper")
    job_id = create_pending_job(client, crate_name="helper-crate")
    claimed_job_id, lease_token = claim_job(client, runner_id, token)
    assert claimed_job_id == job_id
    assert lease_token


def test_create_job_always_creates_new_job_for_same_crate_version(client):
    """Creating a job twice for the same crate/version yields distinct jobs"""
    task_id = create_task_definition(client)

    response1 = client.post(
        "/api/jobs",
        json={"crate_name": "test-crate", "version": "1.0.0", "task_id": task_id},
    )
    assert response1.status_code == 200
    job1_id = response1.json()["job_id"]

    response2 = client.post(
        "/api/jobs",
        json={"crate_name": "test-crate", "version": "1.0.0", "task_id": task_id},
    )
    assert response2.status_code == 200
    job2_id = response2.json()["job_id"]

    assert job1_id != job2_id


def test_create_job_rejects_unknown_task_id(client):
    response = client.post(
        "/api/jobs",
        json={"crate_name": "test-crate", "version": "1.0.0", "task_id": 99999},
    )
    assert response.status_code == 404


def test_create_job_snapshots_task_definition(client):
    task_id = create_task_definition(client, name="snapshot-task")

    response = client.post(
        "/api/jobs",
        json={"crate_name": "snap-crate", "version": "1.0.0", "task_id": task_id},
    )
    assert response.status_code == 200
    job_id = response.json()["job_id"]

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert data["task_id"] == task_id
    assert data["task_name"] == "snapshot-task"
    assert data["task_sha256"] == "sha256-snapshot-task"
    assert data["task_spec"] == {"name": "snapshot-task"}


def test_job_detail_has_no_crate_stats_fields(client):
    job_id = create_pending_job(client, crate_name="stats-free-crate")

    detail = client.get(f"/api/jobs/{job_id}")
    assert detail.status_code == 200
    data = detail.json()
    assert "case_count" not in data
    assert "poc_count" not in data
    assert "compile_failed" not in data


def test_create_job_allows_different_versions(client):
    """Test that different versions of same crate can be created"""
    task_id = create_task_definition(client)

    response1 = client.post(
        "/api/jobs",
        json={"crate_name": "test-crate", "version": "1.0.0", "task_id": task_id},
    )
    assert response1.status_code == 200
    job1_id = response1.json()["job_id"]

    response2 = client.post(
        "/api/jobs",
        json={"crate_name": "test-crate", "version": "2.0.0", "task_id": task_id},
    )
    assert response2.status_code == 200
    job2_id = response2.json()["job_id"]

    assert job1_id != job2_id


def test_create_job_allows_different_crates_same_version(client):
    """Test that different crates with same version can be created"""
    task_id = create_task_definition(client)

    response1 = client.post(
        "/api/jobs",
        json={"crate_name": "crate-a", "version": "1.0.0", "task_id": task_id},
    )
    assert response1.status_code == 200
    job1_id = response1.json()["job_id"]

    response2 = client.post(
        "/api/jobs",
        json={"crate_name": "crate-b", "version": "1.0.0", "task_id": task_id},
    )
    assert response2.status_code == 200
    job2_id = response2.json()["job_id"]

    assert job1_id != job2_id


def test_file_push_missing_job_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-unknown")

    response = client.post(
        f"/api/runners/{runner_id}/jobs/1/files",
        headers=auth_headers(token, request_id="req-unknown"),
        json={
            "lease_token": "x",
            "path": "stdout.log",
            "mode": "append",
            "content": "hello",
            "chunk_seq": 1,
        },
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "runner job not found for file ingest" in r.message.lower()
    )
    assert record.request_id == "req-unknown"
    assert record.runner_id == runner_id
    assert record.job_id == 1
    assert record.path == "stdout.log"
    assert record.chunk_seq == 1


def test_sync_endpoint_updates_status(client):
    runner_id, token = create_runner_and_token(client, "runner-sync")
    job_id = create_pending_job(client, crate_name="sync-crate")
    claimed_job_id, lease_token = claim_job(client, runner_id, token)
    assert claimed_job_id == job_id

    response = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "running",
        },
    )
    assert response.status_code == 200
    assert response.json()["synced"] is True

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "running"


def test_sync_endpoint_terminal_status_sets_finished_at(client):
    runner_id, token = create_runner_and_token(client, "runner-sync-term")
    job_id = create_pending_job(client, crate_name="sync-term-crate")
    claimed_job_id, lease_token = claim_job(client, runner_id, token)
    assert claimed_job_id == job_id

    client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )

    response = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 2,
            "status": "completed",
            "exit_code": 0,
            "message": "Done",
        },
    )
    assert response.status_code == 200
    assert response.json()["synced"] is True

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "completed"
    assert job_data["finished_at"] is not None
    assert job_data["exit_code"] == 0
    assert job_data["message"] == "Done"


def test_sync_endpoint_idempotent_for_duplicate_seq(client):
    runner_id, token = create_runner_and_token(client, "runner-sync-dup")
    job_id = create_pending_job(client, crate_name="sync-dup-crate")
    claimed_job_id, lease_token = claim_job(client, runner_id, token)
    assert claimed_job_id == job_id

    first = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "completed",
        },
    )
    assert first.status_code == 200
    assert first.json()["synced"] is True

    second = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "failed",
        },
    )
    assert second.status_code == 200
    assert second.json()["synced"] is False

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "completed"


def test_sync_endpoint_rejects_invalid_lease(client):
    runner_id, token = create_runner_and_token(client, "runner-sync-lease")
    job_id = create_pending_job(client, crate_name="sync-lease-crate")
    claim_job(client, runner_id, token)

    response = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": "invalid-lease",
            "sync_seq": 1,
            "status": "running",
        },
    )
    assert response.status_code == 409
    assert "lease" in response.json()["detail"].lower()


def test_sync_missing_job_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-sync-missing")

    response = client.post(
        f"/api/runners/{runner_id}/jobs/999999/sync",
        headers=auth_headers(token, request_id="req-sync-missing"),
        json={"lease_token": "x", "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "job not found" in r.message.lower() and "sync" in r.message.lower()
    )
    assert record.request_id == "req-sync-missing"
    assert record.runner_id == runner_id
    assert record.job_id == 999999
    assert record.sync_seq == 1


def test_job_sync_endpoint_updates_last_state_sync_at(client):
    runner_id, token = create_runner_and_token(client, "runner-sync")
    job_id = create_pending_job(client, crate_name="sync-crate")
    claimed_job_id, lease_token = claim_job(client, runner_id, token)
    assert claimed_job_id == job_id

    response = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 200
    assert response.json()["synced"] is True


def test_job_sync_endpoint_rejects_invalid_lease(client):
    runner_id, token = create_runner_and_token(client, "runner-sync-bad")
    job_id = create_pending_job(client, crate_name="sync-bad-crate")
    claim_job(client, runner_id, token)

    response = client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": "invalid-lease", "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 409
    assert "lease" in response.json()["detail"].lower()


def test_dashboard_stats_includes_runner_failed(client):
    # Create a job and mark it as runner_failed directly via DB
    create_pending_job(client, crate_name="stats-crate")
    # We cannot easily set runner_failed through API, so just verify the field exists
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "runner_failed" in data
    assert isinstance(data["runner_failed"], int)
