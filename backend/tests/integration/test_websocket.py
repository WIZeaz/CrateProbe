import pytest
import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.models import TaskStatus


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


def test_websocket_task_updates(client, app):
    """Test WebSocket connection for task updates"""
    # Create a task first
    response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = response.json()["task_id"]

    # Connect to WebSocket
    with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
        # Should receive initial task state
        data = websocket.receive_json()
        assert "id" in data
        assert data["id"] == task_id
        assert data["status"] == "pending"
        assert "runner_id" in data


def test_websocket_task_not_found(client):
    """Test WebSocket connection for non-existent task"""
    with pytest.raises(Exception):  # WebSocket should close/reject
        with client.websocket_connect("/ws/tasks/9999") as websocket:
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


def test_websocket_task_update_event_contains_runner_id(client):
    runner_id = "runner-ws-update"
    token = _create_runner(client, runner_id)
    response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = response.json()["task_id"]
    claim_resp = client.post(
        f"/api/runners/{runner_id}/claim", headers=_runner_headers(token)
    )
    lease_token = claim_resp.json()["lease_token"]

    with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
        _ = websocket.receive_json()
        event_resp = client.post(
            f"/api/runners/{runner_id}/tasks/{task_id}/events",
            headers=_runner_headers(token),
            json={"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
        )
        assert event_resp.status_code == 200
        update_payload = websocket.receive_json()
        assert "runner_id" in update_payload


def test_websocket_dashboard_task_created_and_completed_events_include_runner_id(
    client,
):
    runner_id = "runner-ws-dashboard"
    token = _create_runner(client, runner_id)

    with client.websocket_connect("/ws/dashboard") as websocket:
        _ = websocket.receive_json()

        create_resp = client.post(
            "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
        )
        task_id = create_resp.json()["task_id"]
        created_payload = websocket.receive_json()
        assert created_payload.get("type") == "task_created"
        assert "runner_id" in created_payload

        claim_resp = client.post(
            f"/api/runners/{runner_id}/claim", headers=_runner_headers(token)
        )
        assert claim_resp.status_code == 200
        lease_token = claim_resp.json()["lease_token"]

        event_resp = client.post(
            f"/api/runners/{runner_id}/tasks/{task_id}/events",
            headers=_runner_headers(token),
            json={
                "lease_token": lease_token,
                "event_seq": 1,
                "event_type": "completed",
            },
        )
        assert event_resp.status_code == 200
        completed_payload = websocket.receive_json()
        assert completed_payload.get("type") == "task_completed"
        assert "runner_id" in completed_payload
