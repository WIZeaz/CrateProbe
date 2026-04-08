from fastapi.testclient import TestClient
import pytest

from app.config import Config
from app.main import create_app


@pytest.fixture
def config(tmp_path):
    cfg = Config(
        workspace_path=tmp_path / "workspace",
        admin_token="admin-secret-token",
        lease_ttl_seconds=60,
    )
    cfg.ensure_workspace_structure()
    return cfg


@pytest.fixture
def app(config):
    return create_app(config, str(config.get_db_full_path()))


@pytest.fixture
def client(app):
    return TestClient(app)


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


def test_heartbeat_rejects_invalid_token(client):
    _create_runner(client, "runner-heartbeat-1")

    response = client.post(
        "/api/runners/runner-heartbeat-1/heartbeat",
        headers=_runner_headers("wrong-token"),
    )

    assert response.status_code == 403


def test_claim_returns_204_when_no_pending_tasks(client):
    token = _create_runner(client, "runner-claim-empty")

    response = client.post(
        "/api/runners/runner-claim-empty/claim",
        headers=_runner_headers(token),
    )

    assert response.status_code == 204
    assert response.text == ""


def test_claim_assigns_pending_task_and_returns_lease_token(client):
    token = _create_runner(client, "runner-claim-task")
    create_task_response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    assert create_task_response.status_code == 200
    task_id = create_task_response.json()["task_id"]

    claim_response = client.post(
        "/api/runners/runner-claim-task/claim",
        headers=_runner_headers(token),
    )

    assert claim_response.status_code == 200
    claim_data = claim_response.json()
    assert claim_data["id"] == task_id
    assert claim_data["status"] == "running"
    assert claim_data["runner_id"] == "runner-claim-task"
    assert claim_data["lease_token"]
    assert claim_data["lease_expires_at"] is not None

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "running"
