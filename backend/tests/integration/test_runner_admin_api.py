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


def test_delete_runner_removes_runner_from_list(client):
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
    assert data["enabled"] is True

    list_response = client.get("/api/admin/runners", headers=_admin_headers())
    assert list_response.status_code == 200
    runner_ids = {runner["runner_id"] for runner in list_response.json()}
    assert "runner-delete" not in runner_ids


def test_disable_runner_endpoint_sets_enabled_false(client):
    create_response = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-disable"},
    )
    assert create_response.status_code == 201

    disable_response = client.post(
        "/api/admin/runners/runner-disable/disable",
        headers=_admin_headers(),
    )

    assert disable_response.status_code == 200
    data = disable_response.json()
    assert data["runner_id"] == "runner-disable"
    assert data["enabled"] is False


def test_enable_runner_endpoint_sets_enabled_true_after_disable(client):
    create_response = client.post(
        "/api/admin/runners",
        headers=_admin_headers(),
        json={"runner_id": "runner-enable"},
    )
    assert create_response.status_code == 201

    disable_response = client.post(
        "/api/admin/runners/runner-enable/disable",
        headers=_admin_headers(),
    )
    assert disable_response.status_code == 200
    assert disable_response.json()["enabled"] is False

    enable_response = client.post(
        "/api/admin/runners/runner-enable/enable",
        headers=_admin_headers(),
    )

    assert enable_response.status_code == 200
    data = enable_response.json()
    assert data["runner_id"] == "runner-enable"
    assert data["enabled"] is True


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


def test_head_runners_with_valid_token_returns_200(client):
    response = client.head("/api/admin/runners", headers=_admin_headers())
    assert response.status_code == 200


def test_head_runners_with_invalid_token_returns_403(client):
    response = client.head(
        "/api/admin/runners", headers={"X-Admin-Token": "wrong-token"}
    )
    assert response.status_code == 403
