from fastapi.testclient import TestClient
import pytest

from app.config import Config
from app.main import create_app


@pytest.fixture
def config(tmp_path):
    cfg = Config(
        workspace_path=tmp_path / "workspace", admin_token="admin-secret-token"
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


def test_create_runner_success_and_returns_plaintext_token(client):
    response = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-alpha"},
    )

    assert response.status_code == 201
    data = response.json()

    assert data["runner_id"] == "runner-alpha"
    assert data["enabled"] is True
    assert data["token"].startswith("rnr_")
    assert data["token_hint"] != data["token"]
    assert data["token"][-4:] in data["token_hint"]


def test_invalid_admin_token_rejected(client):
    response = client.post(
        "/api/admin/runners",
        headers={"X-Admin-Token": "wrong-token"},
        json={"runner_id": "runner-alpha"},
    )

    assert response.status_code == 403


def test_delete_runner_soft_disables(client):
    create_response = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-delete"},
    )
    assert create_response.status_code == 201

    delete_response = client.delete(
        "/api/admin/runners/runner-delete",
        headers=_admin_headers(),
    )

    assert delete_response.status_code == 200
    data = delete_response.json()
    assert data["runner_id"] == "runner-delete"
    assert data["enabled"] is False


def test_list_runners_returns_created_runners(client):
    create_a = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-list-a"},
    )
    create_b = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-list-b"},
    )

    assert create_a.status_code == 201
    assert create_b.status_code == 201

    list_response = client.get("/api/admin/runners", headers=_admin_headers())

    assert list_response.status_code == 200
    runners = list_response.json()
    ids = {runner["runner_id"] for runner in runners}

    assert "runner-list-a" in ids
    assert "runner-list-b" in ids
    for runner in runners:
        assert "enabled" in runner
        assert "last_seen_at" in runner
