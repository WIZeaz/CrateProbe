import pytest
import json
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.models import TaskStatus


@pytest.fixture
def config(tmp_path):
    cfg = Config(workspace_path=tmp_path / "workspace")
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
    response = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = response.json()["task_id"]

    # Connect to WebSocket
    with client.websocket_connect(f"/ws/tasks/{task_id}") as websocket:
        # Should receive initial task state
        data = websocket.receive_json()
        assert "id" in data
        assert data["id"] == task_id
        assert data["status"] == "pending"


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
