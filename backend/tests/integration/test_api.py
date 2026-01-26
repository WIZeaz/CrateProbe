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
    """Test deleting non-running task succeeds"""
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    response = client.delete(f"/api/tasks/{task_id}")

    assert response.status_code == 200
    assert response.json()["message"] == "Task deleted"

    # Verify task is actually deleted
    get_response = client.get(f"/api/tasks/{task_id}")
    assert get_response.status_code == 404


def test_dashboard_stats(client, app):
    """Test getting dashboard statistics"""
    # Create some tasks with different statuses
    from app.models import TaskStatus

    # Access the database through the app's dependency injection
    db = app.dependency_overrides.get("get_db")
    if not db:
        # Create tasks via API
        client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
        client.post("/api/tasks", json={"crate_name": "tokio", "version": "1.0.0"})

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


def test_get_task_stdout_logs(client, config):
    """Test getting stdout logs for a task"""
    # Create a task
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Create a fake log file
    log_file = config.workspace_path / "logs" / f"serde-1.0.0-stdout.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("\n".join([f"Log line {i}" for i in range(1, 21)]))

    # Get last 10 lines
    response = client.get(f"/api/tasks/{task_id}/logs/stdout?lines=10")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert len(data["lines"]) == 10
    assert data["lines"][0] == "Log line 11"
    assert data["lines"][-1] == "Log line 20"


def test_get_task_stderr_logs(client, config):
    """Test getting stderr logs for a task"""
    create_resp = client.post("/api/tasks", json={"crate_name": "tokio", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Create a fake log file
    log_file = config.workspace_path / "logs" / f"tokio-1.0.0-stderr.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("Error line 1\nError line 2\nError line 3")

    response = client.get(f"/api/tasks/{task_id}/logs/stderr?lines=100")

    assert response.status_code == 200
    data = response.json()
    assert len(data["lines"]) == 3


def test_get_task_logs_not_found(client):
    """Test getting logs for non-existent task"""
    response = client.get("/api/tasks/9999/logs/stdout")

    assert response.status_code == 404


def test_get_task_logs_file_missing(client, config):
    """Test getting logs when file doesn't exist"""
    # Use serde which exists
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Don't create the log file - it should be missing
    response = client.get(f"/api/tasks/{task_id}/logs/stdout")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_download_stdout_raw(client, config):
    """Test downloading full stdout log"""
    # Use serde which exists
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Create a fake log file
    log_file = config.workspace_path / "logs" / f"serde-1.0.0-stdout.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_content = "Full log content\nLine 2\nLine 3"
    log_file.write_text(log_content)

    response = client.get(f"/api/tasks/{task_id}/logs/stdout/raw")

    assert response.status_code == 200


def test_get_task_realtime_stats(client, config):
    """Test getting real-time test case and POC counts"""
    # Create a task
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Build workspace path from config (same as task creation logic)
    workspace_path = config.workspace_path / "repos" / "serde-1.0.0"
    testgen_dir = workspace_path / "testgen"
    tests_dir = testgen_dir / "tests"
    poc_dir = testgen_dir / "poc"

    tests_dir.mkdir(parents=True, exist_ok=True)
    poc_dir.mkdir(parents=True, exist_ok=True)

    # Create some test cases and POCs
    (tests_dir / "case1").mkdir()
    (tests_dir / "case2").mkdir()
    (tests_dir / "case3").mkdir()
    (poc_dir / "poc1").mkdir()
    (poc_dir / "poc2").mkdir()

    # Get realtime stats
    response = client.get(f"/api/tasks/{task_id}/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["case_count"] == 3
    assert data["poc_count"] == 2


def test_get_task_realtime_stats_empty(client, config):
    """Test getting real-time stats when testgen directory doesn't exist"""
    # Create a task
    create_resp = client.post("/api/tasks", json={"crate_name": "serde", "version": "1.0.0"})
    task_id = create_resp.json()["task_id"]

    # Get realtime stats without creating testgen directory
    response = client.get(f"/api/tasks/{task_id}/stats")

    assert response.status_code == 200
    data = response.json()
    assert data["case_count"] == 0
    assert data["poc_count"] == 0


def test_get_task_realtime_stats_not_found(client):
    """Test getting stats for non-existent task"""
    response = client.get("/api/tasks/99999/stats")

    assert response.status_code == 404
