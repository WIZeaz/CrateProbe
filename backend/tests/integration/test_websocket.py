import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config


def _admin_headers() -> dict[str, str]:
    return {"X-Admin-Token": "admin-secret-token"}


def _create_runner(client: TestClient, runner_id: str = "runner-control-1") -> str:
    response = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": runner_id},
    )
    assert response.status_code == 201
    return response.json()["token"]


def _runner_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def config(tmp_path):
    cfg = Config(
        workspace_path=tmp_path / "workspace",
        admin_token="admin-secret-token",
    )
    cfg.ensure_workspace_structure()
    return cfg


@pytest.fixture
def app(config):
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


def _create_job(client: TestClient, app, crate_name="serde", version="1.0.0") -> int:
    db = app.state.scheduler.db
    task_id = db.create_task_definition(
        name="cargo-rapx",
        desc="test task",
        sha256=f"sha256-for-{crate_name}",
        zip_path="/tmp/task.zip",
        spec='{"name": "cargo-rapx"}',
    )
    response = client.post(
        "/api/jobs",
        json={"crate_name": crate_name, "version": version, "task_id": task_id},
    )
    assert response.status_code == 200
    return response.json()["job_id"]


def test_websocket_job_updates(client, app):
    """Test WebSocket connection for job updates"""
    job_id = _create_job(client, app)

    # Connect to WebSocket
    with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
        # Should receive initial job state
        data = websocket.receive_json()
        assert "id" in data
        assert data["id"] == job_id
        assert data["status"] == "pending"
        assert "runner_id" in data


def test_websocket_job_not_found(client):
    """Test WebSocket connection for non-existent job"""
    with pytest.raises(Exception):  # WebSocket should close/reject
        with client.websocket_connect("/ws/jobs/9999") as websocket:
            pass


def test_websocket_dashboard_updates(client):
    """Test WebSocket connection for dashboard updates"""
    # Connect to dashboard WebSocket
    with client.websocket_connect("/ws/dashboard") as websocket:
        # Should receive initial stats
        data = websocket.receive_json()
        assert "total" in data
        assert "pending" in data
        assert "running" in data
        assert "completed" in data
        assert isinstance(data["total"], int)


def test_websocket_job_update_event_contains_runner_id(client, app):
    runner_id = "runner-ws-update"
    token = _create_runner(client, runner_id)
    job_id = _create_job(client, app)
    claim_resp = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    lease_token = claim_resp.json()["lease_token"]

    with client.websocket_connect(f"/ws/jobs/{job_id}") as websocket:
        _ = websocket.receive_json()
        sync_resp = client.post(
            f"/api/runners/{runner_id}/jobs/{job_id}/sync",
            headers=_runner_headers(token),
            json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
        )
        assert sync_resp.status_code == 200
        update_payload = websocket.receive_json()
        assert "runner_id" in update_payload


def test_websocket_dashboard_job_created_and_completed_events_include_runner_id(
    client, app
):
    runner_id = "runner-ws-dashboard"
    token = _create_runner(client, runner_id)

    with client.websocket_connect("/ws/dashboard") as websocket:
        _ = websocket.receive_json()

        job_id = _create_job(client, app)
        created_payload = websocket.receive_json()
        assert created_payload.get("type") == "job_created"
        assert "runner_id" in created_payload

        claim_resp = client.post(
            f"/api/runners/{runner_id}/claim",
            headers=_runner_headers(token),
            json={"jobs": 0, "max_jobs": 1},
        )
        assert claim_resp.status_code == 200
        lease_token = claim_resp.json()["lease_token"]

        sync_resp = client.post(
            f"/api/runners/{runner_id}/jobs/{job_id}/sync",
            headers=_runner_headers(token),
            json={
                "lease_token": lease_token,
                "sync_seq": 1,
                "status": "completed",
            },
        )
        assert sync_resp.status_code == 200
        completed_payload = websocket.receive_json()
        assert completed_payload.get("type") == "job_completed"
        assert "runner_id" in completed_payload
