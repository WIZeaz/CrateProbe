import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config


@pytest.fixture
def test_config(tmp_path):
    """Create a test configuration"""
    config = Config(
        workspace_path=tmp_path / "workspace",
        max_memory_gb=1,
        max_runtime_seconds=3600,
        max_jobs=1,
        server_host="127.0.0.1",
        server_port=8000,
        log_level="INFO",
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
