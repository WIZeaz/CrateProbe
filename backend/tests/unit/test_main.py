import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
import app.main as main_module


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


def test_log_chunk_unknown_type_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-unknown")

    response = client.post(
        f"/api/runners/{runner_id}/tasks/1/logs/unknown/chunks",
        headers=auth_headers(token, request_id="req-unknown"),
        json={"lease_token": "x", "chunk_seq": 1, "content": "hello"},
    )

    assert response.status_code == 404
    record = next(r for r in caplog.records if "unknown log type" in r.message.lower())
    assert record.request_id == "req-unknown"
    assert record.runner_id == runner_id
    assert record.task_id == 1
    assert record.log_type == "unknown"
    assert record.chunk_seq == 1


def test_event_lease_mismatch_logs_warning_with_fields(client, monkeypatch, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-lease")
    task_id = create_pending_task(client, monkeypatch, crate_name="lease-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/events",
        headers=auth_headers(token),
        json={
            "lease_token": f"wrong-{lease_token}",
            "event_seq": 1,
            "event_type": "progress",
        },
    )

    assert response.status_code == 409
    record = next(
        r for r in caplog.records if "lease token mismatch" in r.message.lower()
    )
    assert record.runner_id == runner_id
    assert record.task_id == task_id
    assert record.event_seq == 1
    assert isinstance(record.request_id, str)
    assert len(record.request_id) == 12


def test_event_not_applied_logs_info_with_fields(client, monkeypatch, caplog):
    caplog.set_level("INFO")
    runner_id, token = create_runner_and_token(client, "runner-not-applied")
    task_id = create_pending_task(client, monkeypatch, crate_name="not-applied-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    monkeypatch.setattr(
        main_module.Database, "apply_task_event", lambda *_a, **_k: False
    )
    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/events",
        headers=auth_headers(token, request_id="req-not-applied"),
        json={"lease_token": lease_token, "event_seq": 2, "event_type": "progress"},
    )

    assert response.status_code == 200
    assert response.json()["applied"] is False
    record = next(r for r in caplog.records if "event not applied" in r.message.lower())
    assert record.request_id == "req-not-applied"
    assert record.runner_id == runner_id
    assert record.task_id == task_id
    assert record.event_seq == 2


def test_event_missing_task_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-missing-event")

    response = client.post(
        f"/api/runners/{runner_id}/tasks/999999/events",
        headers=auth_headers(token, request_id="req-missing-event"),
        json={"lease_token": "x", "event_seq": 1, "event_type": "progress"},
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "task not found" in r.message.lower() and "event" in r.message.lower()
    )
    assert record.request_id == "req-missing-event"
    assert record.runner_id == runner_id
    assert record.task_id == 999999
    assert record.event_seq == 1


def test_log_ingest_missing_task_logs_warning_with_fields(client, caplog):
    caplog.set_level("WARNING")
    runner_id, token = create_runner_and_token(client, "runner-missing-log")

    response = client.post(
        f"/api/runners/{runner_id}/tasks/999999/logs/stdout/chunks",
        headers=auth_headers(token, request_id="req-missing-log"),
        json={"lease_token": "x", "chunk_seq": 1, "content": "hello"},
    )

    assert response.status_code == 404
    record = next(
        r
        for r in caplog.records
        if "task not found" in r.message.lower() and "log" in r.message.lower()
    )
    assert record.request_id == "req-missing-log"
    assert record.runner_id == runner_id
    assert record.task_id == 999999
    assert record.log_type == "stdout"
    assert record.chunk_seq == 1
