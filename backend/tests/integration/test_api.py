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


def test_root_redirect(client):
    """Test root redirects to docs"""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307


def test_create_task_with_version(client):
    """Test creating a task with explicit version"""
    response = client.post(
        "/api/tasks",
        json={"crate_name": "serde", "version": "1.0.0"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["task_id"] == 1
    assert data["crate_name"] == "serde"
    assert data["version"] == "1.0.0"
    assert data["status"] == "pending"


def test_get_all_tasks(client):
    """Test retrieving all tasks"""
    # Create a task first
    client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})

    response = client.get("/api/tasks")

    assert response.status_code == 200
    tasks = response.json()
    assert len(tasks) == 1
    assert tasks[0]["crate_name"] == "serde"


def test_get_task_by_id(client):
    """Test retrieving specific task"""
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == task_id
    assert data["crate_name"] == "serde"


def test_delete_task_not_running(client):
    """Test deleting non-running task returns error"""
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 400
