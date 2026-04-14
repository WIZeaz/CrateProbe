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


def _create_and_claim_task(
    client: TestClient,
    runner_id: str,
) -> tuple[int, str, str]:
    token = _create_runner(client, runner_id)
    create_task_response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    assert create_task_response.status_code == 200
    task_id = create_task_response.json()["task_id"]

    claim_response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=_runner_headers(token),
    )
    assert claim_response.status_code == 200
    lease_token = claim_response.json()["lease_token"]
    return task_id, token, lease_token


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


def test_events_endpoint_is_idempotent_for_duplicate_event_seq(client):
    task_id, token, lease_token = _create_and_claim_task(client, "runner-events-1")

    started_response = client.post(
        f"/api/runners/runner-events-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
    )
    assert started_response.status_code == 200

    completed_response = client.post(
        f"/api/runners/runner-events-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "event_seq": 2, "event_type": "completed"},
    )
    assert completed_response.status_code == 200

    before_duplicate = client.get(f"/api/tasks/{task_id}")
    assert before_duplicate.status_code == 200
    before_data = before_duplicate.json()
    assert before_data["status"] == "completed"
    assert before_data["finished_at"] is not None

    duplicate_response = client.post(
        f"/api/runners/runner-events-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "event_seq": 2, "event_type": "failed"},
    )
    assert duplicate_response.status_code == 200

    after_duplicate = client.get(f"/api/tasks/{task_id}")
    assert after_duplicate.status_code == 200
    after_data = after_duplicate.json()
    assert after_data["status"] == "completed"
    assert after_data["finished_at"] == before_data["finished_at"]


def test_logs_endpoint_ignores_duplicate_chunk_seq_and_writes_once(client):
    task_id, token, lease_token = _create_and_claim_task(client, "runner-logs-1")

    first_chunk = client.post(
        f"/api/runners/runner-logs-1/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "hello\n"},
    )
    assert first_chunk.status_code == 200

    duplicate_chunk = client.post(
        f"/api/runners/runner-logs-1/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "ignored\n"},
    )
    assert duplicate_chunk.status_code == 200

    raw_log = client.get(f"/api/tasks/{task_id}/logs/stdout/raw")
    assert raw_log.status_code == 200
    assert raw_log.text == "hello\n"


def test_events_endpoint_returns_409_for_lease_mismatch(client):
    task_id, token, _lease_token = _create_and_claim_task(client, "runner-lease-1")

    response = client.post(
        f"/api/runners/runner-lease-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": "wrong-lease", "event_seq": 1, "event_type": "started"},
    )

    assert response.status_code == 409
