"""Integration tests for the task definition management API (/api/tasks)."""

import io
import os
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import create_app
from core.task_spec import sha256_hex

TASK_A_TOML = b"""
name = "cargo-rapx"
image = "rapx:latest"
cmd = "cargo rapx"
workspace = "/workspace"
desc = "task A"
"""

TASK_B_TOML = b"""
name = "miri-check"
image = "miri:latest"
cmd = "cargo miri test"
workspace = "/workspace"
desc = "task B"
"""


def build_zip(entries: dict) -> bytes:
    """Build an in-memory zip from {name: bytes|str} entries."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return buffer.getvalue()


def task_zip(toml: bytes = TASK_A_TOML, extra: dict | None = None) -> bytes:
    entries = {"task.toml": toml}
    if extra:
        entries.update(extra)
    return build_zip(entries)


def _admin_headers() -> dict:
    return {"X-Admin-Token": "admin-secret-token"}


def make_client(tmp_path, **config_kwargs) -> TestClient:
    cfg = Config(
        workspace_path=tmp_path / "workspace",
        admin_token="admin-secret-token",
        **config_kwargs,
    )
    cfg.ensure_workspace_structure()
    app = create_app(cfg, str(cfg.get_db_full_path()))
    return TestClient(app)


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


def upload(client, content: bytes, headers=None):
    return client.post(
        "/api/tasks",
        files={"file": ("task.zip", content, "application/zip")},
        headers=headers if headers is not None else _admin_headers(),
    )


def test_upload_list_detail_flow(client, config):
    zip_bytes = task_zip()
    response = upload(client, zip_bytes)

    assert response.status_code == 200
    data = response.json()
    assert data["existed"] is False
    assert data["name"] == "cargo-rapx"
    assert data["desc"] == "task A"
    assert data["sha256"] == sha256_hex(zip_bytes)
    assert data["spec"]["name"] == "cargo-rapx"
    assert data["spec"]["cmd"] == "cargo rapx"
    assert data["spec"]["image"] == "rapx:latest"
    assert data["created_at"]
    assert data["updated_at"] is None

    # Zip landed on disk under workspace/tasks/{sha256}.zip
    zip_path = config.workspace_path / "tasks" / f"{data['sha256']}.zip"
    assert zip_path.read_bytes() == zip_bytes

    # List is public and contains the task
    list_response = client.get("/api/tasks")
    assert list_response.status_code == 200
    tasks = list_response.json()
    assert [t["id"] for t in tasks] == [data["id"]]
    assert tasks[0]["spec"]["name"] == "cargo-rapx"

    # Detail is public and includes the spec dict
    detail = client.get(f"/api/tasks/{data['id']}")
    assert detail.status_code == 200
    detail_data = detail.json()
    assert detail_data["sha256"] == data["sha256"]
    assert detail_data["spec"]["workspace"] == "/workspace"


def test_upload_duplicate_returns_existing_task(client):
    zip_bytes = task_zip()
    first = upload(client, zip_bytes)
    assert first.status_code == 200
    assert first.json()["existed"] is False

    second = upload(client, zip_bytes)
    assert second.status_code == 200
    assert second.json()["existed"] is True
    assert second.json()["id"] == first.json()["id"]

    tasks = client.get("/api/tasks").json()
    assert len(tasks) == 1


def test_upload_zip_without_task_toml_returns_422(client):
    response = upload(client, build_zip({"run.sh": "echo hi"}))
    assert response.status_code == 422


def test_upload_non_zip_returns_422(client):
    response = upload(client, b"definitely not a zip")
    assert response.status_code == 422


def test_upload_zip_slip_returns_422(client):
    content = build_zip({"task.toml": TASK_A_TOML, "../evil.sh": "x"})
    response = upload(client, content)
    assert response.status_code == 422


def test_get_unknown_task_returns_404(client):
    assert client.get("/api/tasks/4242").status_code == 404


def test_update_replaces_zip_and_updates_fields(client, config):
    original = upload(client, task_zip(TASK_A_TOML)).json()
    old_sha = original["sha256"]

    new_zip = task_zip(TASK_B_TOML)
    response = client.put(
        f"/api/tasks/{original['id']}",
        files={"file": ("task.zip", new_zip, "application/zip")},
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == original["id"]
    assert data["sha256"] == sha256_hex(new_zip)
    assert data["sha256"] != old_sha
    assert data["name"] == "miri-check"
    assert data["desc"] == "task B"
    assert data["spec"]["cmd"] == "cargo miri test"
    assert data["updated_at"] is not None

    # Old zip removed, new zip present
    tasks_dir = config.workspace_path / "tasks"
    assert not (tasks_dir / f"{old_sha}.zip").exists()
    assert (tasks_dir / f"{data['sha256']}.zip").read_bytes() == new_zip

    # Detail reflects the update
    detail = client.get(f"/api/tasks/{original['id']}").json()
    assert detail["sha256"] == data["sha256"]
    assert detail["spec"]["name"] == "miri-check"


def test_update_with_same_sha_returns_200_unchanged(client, config):
    zip_bytes = task_zip(TASK_A_TOML)
    original = upload(client, zip_bytes).json()

    response = client.put(
        f"/api/tasks/{original['id']}",
        files={"file": ("task.zip", zip_bytes, "application/zip")},
        headers=_admin_headers(),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["sha256"] == original["sha256"]
    assert data["updated_at"] is None
    zip_path = config.workspace_path / "tasks" / f"{original['sha256']}.zip"
    assert zip_path.read_bytes() == zip_bytes


def test_update_conflicting_sha_returns_409(client, config):
    task_a = upload(client, task_zip(TASK_A_TOML)).json()
    task_b = upload(client, task_zip(TASK_B_TOML)).json()

    response = client.put(
        f"/api/tasks/{task_a['id']}",
        files={"file": ("task.zip", task_zip(TASK_B_TOML), "application/zip")},
        headers=_admin_headers(),
    )

    assert response.status_code == 409

    # A is unchanged, B's zip is still on disk
    detail = client.get(f"/api/tasks/{task_a['id']}").json()
    assert detail["sha256"] == task_a["sha256"]
    tasks_dir = config.workspace_path / "tasks"
    assert (tasks_dir / f"{task_b['sha256']}.zip").exists()


def test_update_unknown_task_returns_404(client):
    response = client.put(
        "/api/tasks/4242",
        files={"file": ("task.zip", task_zip(), "application/zip")},
        headers=_admin_headers(),
    )
    assert response.status_code == 404


def test_update_invalid_zip_returns_422(client):
    original = upload(client, task_zip()).json()
    response = client.put(
        f"/api/tasks/{original['id']}",
        files={"file": ("task.zip", b"not a zip", "application/zip")},
        headers=_admin_headers(),
    )
    assert response.status_code == 422


def test_delete_removes_row_and_zip(client, config):
    task = upload(client, task_zip()).json()
    zip_path = config.workspace_path / "tasks" / f"{task['sha256']}.zip"
    assert zip_path.exists()

    response = client.delete(f"/api/tasks/{task['id']}", headers=_admin_headers())

    assert response.status_code == 200
    assert response.json()["id"] == task["id"]
    assert client.get(f"/api/tasks/{task['id']}").status_code == 404
    assert not zip_path.exists()


def test_delete_unknown_task_returns_404(client):
    response = client.delete("/api/tasks/4242", headers=_admin_headers())
    assert response.status_code == 404


def test_write_endpoints_reject_missing_admin_token(client):
    zip_bytes = task_zip()

    assert upload(client, zip_bytes, headers={}).status_code == 403

    task = upload(client, zip_bytes).json()
    put = client.put(
        f"/api/tasks/{task['id']}",
        files={"file": ("task.zip", zip_bytes, "application/zip")},
    )
    assert put.status_code == 403
    assert client.delete(f"/api/tasks/{task['id']}").status_code == 403


def test_write_endpoints_reject_wrong_admin_token(client):
    bad = {"X-Admin-Token": "wrong-token"}
    assert upload(client, task_zip(), headers=bad).status_code == 403


def test_read_endpoints_do_not_require_admin_token(client):
    task = upload(client, task_zip()).json()
    assert client.get("/api/tasks").status_code == 200
    assert client.get(f"/api/tasks/{task['id']}").status_code == 200


def test_upload_over_size_limit_returns_413(tmp_path):
    client = make_client(tmp_path, task_upload_max_mb=1)
    # Random payload is incompressible, so the zip itself exceeds 1 MiB.
    zip_bytes = task_zip(extra={"payload.bin": os.urandom(2 * 1024 * 1024)})
    assert len(zip_bytes) > 1024 * 1024

    response = upload(client, zip_bytes)

    assert response.status_code == 413


def test_upload_at_size_limit_boundary_accepted(tmp_path):
    client = make_client(tmp_path, task_upload_max_mb=1)
    zip_bytes = task_zip()
    assert len(zip_bytes) < 1024 * 1024

    response = upload(client, zip_bytes)

    assert response.status_code == 200


def test_download_example_task(client):
    """GET /api/tasks/example returns a valid example task zip."""
    from core.task_spec import parse_task_spec

    resp = client.get("/api/tasks/example")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "example-task.zip" in resp.headers.get("content-disposition", "")

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
        assert "task.toml" in names
        assert len(names) > 1  # example ships more than just task.toml
        spec = parse_task_spec(zf.read("task.toml"))
        assert spec.name
        assert spec.image
        assert spec.cmd
        assert spec.workspace == "/workspace"


def test_download_task_zip_returns_uploaded_bytes(client):
    """GET /api/tasks/{id}/zip returns the exact uploaded zip bytes."""
    zip_bytes = task_zip()
    task_id = upload(client, zip_bytes).json()["id"]

    resp = client.get(f"/api/tasks/{task_id}/zip")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert resp.content == zip_bytes


def test_get_task_toml_returns_raw_content(client):
    """GET /api/tasks/{id}/task-toml returns the raw task.toml text."""
    task_id = upload(client, task_zip()).json()["id"]

    resp = client.get(f"/api/tasks/{task_id}/task-toml")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == TASK_A_TOML.decode("utf-8")


def test_download_task_zip_unknown_task_returns_404(client):
    assert client.get("/api/tasks/999/zip").status_code == 404


def test_get_task_toml_unknown_task_returns_404(client):
    assert client.get("/api/tasks/999/task-toml").status_code == 404
