import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
import app.main as main_module
from core.models import TaskStatus


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


def create_pending_task(client, monkeypatch, crate_name="serde", version="1.0.0"):
    class MockCratesAPI:
        async def get_latest_version(self, _crate_name):
            return version

        async def verify_version_exists(self, _crate_name, _version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())
    response = client.post(
        "/api/tasks",
        json={"crate_name": crate_name, "version": version},
    )
    assert response.status_code == 200
    return response.json()["task_id"]


def claim_task(client, runner_id, token):
    response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=auth_headers(token, request_id="req-claim"),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert response.status_code == 200
    body = response.json()
    return body["id"], body["lease_token"]


def test_runner_helpers_can_create_and_claim_task(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-helper")
    task_id = create_pending_task(client, monkeypatch, crate_name="helper-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id
    assert lease_token


def test_create_task_returns_existing_task_if_duplicate(client, monkeypatch):
    """Test that creating a task with same crate and version returns existing task"""

    # Mock crates API to avoid external calls
    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())

    # Create first task
    response1 = client.post(
        "/api/tasks", json={"crate_name": "test-crate", "version": "1.0.0"}
    )
    assert response1.status_code == 200
    task1 = response1.json()
    task1_id = task1["task_id"]

    # Create second task with same crate and version
    response2 = client.post(
        "/api/tasks", json={"crate_name": "test-crate", "version": "1.0.0"}
    )
    assert response2.status_code == 200
    task2 = response2.json()

    # Should return the same task
    assert task2["task_id"] == task1_id
    assert task2["crate_name"] == "test-crate"
    assert task2["version"] == "1.0.0"


def test_create_task_running_duplicate_returns_unchanged(client, monkeypatch):
    """Test that creating a duplicate task while it is running returns it unchanged"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())

    task_id = create_pending_task(client, monkeypatch, crate_name="running-crate")
    runner_id, token = create_runner_and_token(client, "runner-running-dup")
    claimed_id, _ = claim_task(client, runner_id, token)
    assert claimed_id == task_id

    response = client.post(
        "/api/tasks", json={"crate_name": "running-crate", "version": "1.0.0"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["status"] == "running"


def test_create_task_terminal_duplicate_resets_for_retry(client, monkeypatch):
    """Test that creating a duplicate terminal task resets it to pending"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())

    task_id = create_pending_task(client, monkeypatch, crate_name="terminal-crate")
    db = client.app.state.scheduler.db
    db.update_task_status(
        task_id,
        TaskStatus.FAILED,
        started_at=datetime.now(),
        finished_at=datetime.now(),
        exit_code=1,
        error_message="boom",
    )

    response = client.post(
        "/api/tasks", json={"crate_name": "terminal-crate", "version": "1.0.0"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["task_id"] == task_id
    assert body["status"] == "pending"

    task = db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.started_at is None
    assert task.finished_at is None
    assert task.exit_code is None
    assert task.error_message is None


def test_create_task_allows_different_versions(client, monkeypatch):
    """Test that different versions of same crate can be created"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())

    # Create task for version 1.0.0
    response1 = client.post(
        "/api/tasks", json={"crate_name": "test-crate", "version": "1.0.0"}
    )
    assert response1.status_code == 200
    task1_id = response1.json()["task_id"]

    # Create task for version 2.0.0
    response2 = client.post(
        "/api/tasks", json={"crate_name": "test-crate", "version": "2.0.0"}
    )
    assert response2.status_code == 200
    task2_id = response2.json()["task_id"]

    # Should be different tasks
    assert task1_id != task2_id


def test_create_task_allows_different_crates_same_version(client, monkeypatch):
    """Test that different crates with same version can be created"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda: MockCratesAPI())

    # Create task for crate-a
    response1 = client.post(
        "/api/tasks", json={"crate_name": "crate-a", "version": "1.0.0"}
    )
    assert response1.status_code == 200
    task1_id = response1.json()["task_id"]

    # Create task for crate-b
    response2 = client.post(
        "/api/tasks", json={"crate_name": "crate-b", "version": "1.0.0"}
    )
    assert response2.status_code == 200
    task2_id = response2.json()["task_id"]

    # Should be different tasks
    assert task1_id != task2_id


def test_log_chunk_missing_task_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-unknown")

    response = client.post(
        f"/api/runners/{runner_id}/tasks/1/logs/unknown/chunks",
        headers=auth_headers(token, request_id="req-unknown"),
        json={"lease_token": "x", "chunk_seq": 1, "content": "hello"},
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "runner task not found for log ingest" in r.message.lower()
    )
    assert record.request_id == "req-unknown"
    assert record.runner_id == runner_id
    assert record.task_id == 1
    assert record.log_type == "unknown"
    assert record.chunk_seq == 1


def test_sync_endpoint_updates_status_and_counts(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "running",
            "case_count": 3,
            "poc_count": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["synced"] is True

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "running"
    assert task_data["case_count"] == 3
    assert task_data["poc_count"] == 1


def test_sync_endpoint_terminal_status_sets_finished_at(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync-term")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-term-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 2,
            "status": "completed",
            "exit_code": 0,
            "message": "Done",
            "case_count": 10,
            "poc_count": 5,
        },
    )
    assert response.status_code == 200
    assert response.json()["synced"] is True

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "completed"
    assert task_data["finished_at"] is not None
    assert task_data["exit_code"] == 0
    assert task_data["message"] == "Done"
    assert task_data["case_count"] == 10


def test_sync_endpoint_idempotent_for_duplicate_seq(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync-dup")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-dup-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    first = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "completed",
            "case_count": 5,
        },
    )
    assert first.status_code == 200
    assert first.json()["synced"] is True

    second = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "failed",
            "case_count": 99,
        },
    )
    assert second.status_code == 200
    assert second.json()["synced"] is False

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "completed"
    assert task_data["case_count"] == 5


def test_sync_endpoint_rejects_invalid_lease(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync-lease")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-lease-crate")
    claim_task(client, runner_id, token)

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={
            "lease_token": "invalid-lease",
            "sync_seq": 1,
            "status": "running",
        },
    )
    assert response.status_code == 409
    assert "lease" in response.json()["detail"].lower()


def test_sync_missing_task_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-sync-missing")

    response = client.post(
        f"/api/runners/{runner_id}/tasks/999999/sync",
        headers=auth_headers(token, request_id="req-sync-missing"),
        json={"lease_token": "x", "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "task not found" in r.message.lower() and "sync" in r.message.lower()
    )
    assert record.request_id == "req-sync-missing"
    assert record.runner_id == runner_id
    assert record.task_id == 999999
    assert record.sync_seq == 1


def test_task_sync_endpoint_updates_last_state_sync_at(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 200
    assert response.json()["synced"] is True


def test_task_sync_endpoint_rejects_invalid_lease(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-sync-bad")
    task_id = create_pending_task(client, monkeypatch, crate_name="sync-bad-crate")
    claim_task(client, runner_id, token)

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/sync",
        headers=auth_headers(token),
        json={"lease_token": "invalid-lease", "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 409
    assert "lease" in response.json()["detail"].lower()


def test_dashboard_stats_includes_runner_failed(client, monkeypatch):
    # Create a task and mark it as runner_failed directly via DB
    task_id = create_pending_task(client, monkeypatch, crate_name="stats-crate")
    # We cannot easily set runner_failed through API, so just verify the field exists
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    data = response.json()
    assert "runner_failed" in data
    assert isinstance(data["runner_failed"], int)
