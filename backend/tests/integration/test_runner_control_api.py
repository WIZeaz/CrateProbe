import io
import zipfile

from fastapi.testclient import TestClient
import pytest
import time

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


@pytest.fixture(autouse=True)
def mock_crates_api(monkeypatch):
    """Avoid real crates.io calls in tests."""

    class MockCratesAPI:
        async def get_latest_version(self, _crate_name):
            return "1.0.0"

        async def verify_version_exists(self, _crate_name, _version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr("app.main.CratesAPI", lambda **kwargs: MockCratesAPI())


@pytest.fixture
def short_lease_client(tmp_path):
    cfg = Config(
        workspace_path=tmp_path / "workspace-short-lease",
        admin_token="admin-secret-token",
        lease_ttl_seconds=1,
    )
    cfg.ensure_workspace_structure()
    short_lease_app = create_app(cfg, str(cfg.get_db_full_path()))

    with TestClient(short_lease_app) as short_lease_test_client:
        yield cfg, short_lease_app, short_lease_test_client


@pytest.fixture
def small_caps_client(tmp_path):
    """Client whose file/artifact caps are 1 MiB so limits are testable."""
    cfg = Config(
        workspace_path=tmp_path / "workspace-small-caps",
        admin_token="admin-secret-token",
        lease_ttl_seconds=60,
        file_push_max_mb=1,
        artifact_max_mb=1,
    )
    cfg.ensure_workspace_structure()
    app = create_app(cfg, str(cfg.get_db_full_path()))
    return cfg, TestClient(app)


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


def _create_task_definition(app, name="cargo-rapx") -> int:
    db = app.state.scheduler.db
    return db.create_task_definition(
        name=name,
        desc="test task",
        sha256=f"sha256-{name}",
        zip_path=f"/tmp/{name}.zip",
        spec='{"name": "%s"}' % name,
    )


def _create_job_with_task(
    client: TestClient, app, crate_name="serde", version="1.0.0"
) -> tuple[int, int]:
    task_id = _create_task_definition(app, name=f"task-for-{crate_name}")
    response = client.post(
        "/api/jobs",
        json={"crate_name": crate_name, "version": version, "task_id": task_id},
    )
    assert response.status_code == 200
    return response.json()["job_id"], task_id


def _create_job(client: TestClient, app, crate_name="serde", version="1.0.0") -> int:
    job_id, _task_id = _create_job_with_task(client, app, crate_name, version)
    return job_id


def _build_task_zip(toml: bytes, extra: dict | None = None) -> bytes:
    entries = {"task.toml": toml}
    if extra:
        entries.update(extra)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return buffer.getvalue()


def _upload_task(client: TestClient, zip_bytes: bytes) -> int:
    response = client.post(
        "/api/tasks",
        files={"file": ("task.zip", zip_bytes, "application/zip")},
        headers=_admin_headers(),
    )
    assert response.status_code == 200
    return response.json()["id"]


def _files_dir(config, job_id: int):
    return config.workspace_path / "jobs" / str(job_id) / "files"


def _artifacts_dir(config, job_id: int):
    return config.workspace_path / "jobs" / str(job_id) / "artifacts"


def _push_file(
    client: TestClient,
    runner_id: str,
    token: str,
    job_id: int,
    **payload,
):
    return client.post(
        f"/api/runners/{runner_id}/jobs/{job_id}/files",
        headers=_runner_headers(token),
        json=payload,
    )


def _create_and_claim_job(
    client: TestClient,
    runner_id: str,
) -> tuple[int, str, str]:
    token = _create_runner(client, runner_id)
    job_id = _create_job(client, client.app)

    claim_response = client.post(
        f"/api/runners/{runner_id}/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert claim_response.status_code == 200
    lease_token = claim_response.json()["lease_token"]
    return job_id, token, lease_token


def _reconcile_until_pending(
    app,
    client: TestClient,
    job_id: int,
    timeout_seconds: float = 5.0,
    interval_seconds: float = 0.1,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.state.scheduler.reconcile_expired_leases()
        response = client.get(f"/api/jobs/{job_id}")
        assert response.status_code == 200
        job_data = response.json()
        if job_data["status"] == "pending":
            return job_data
        time.sleep(interval_seconds)

    pytest.fail(f"Job {job_id} did not return to pending before timeout")


def test_heartbeat_rejects_invalid_token(client):
    _create_runner(client, "runner-heartbeat-1")

    response = client.post(
        "/api/runners/runner-heartbeat-1/heartbeat",
        headers=_runner_headers("wrong-token"),
    )

    assert response.status_code == 403


def test_claim_returns_204_when_no_pending_jobs(client):
    token = _create_runner(client, "runner-claim-empty")

    response = client.post(
        "/api/runners/runner-claim-empty/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )

    assert response.status_code == 204
    assert response.text == ""


def test_claim_assigns_pending_job_and_returns_lease_token(client):
    token = _create_runner(client, "runner-claim-job")
    job_id, task_id = _create_job_with_task(client, client.app)

    claim_response = client.post(
        "/api/runners/runner-claim-job/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )

    assert claim_response.status_code == 200
    claim_data = claim_response.json()
    assert claim_data["job_id"] == job_id
    assert "id" not in claim_data
    assert claim_data["crate_name"] == "serde"
    assert claim_data["version"] == "1.0.0"
    assert claim_data["status"] == "running"
    assert claim_data["runner_id"] == "runner-claim-job"
    assert claim_data["lease_token"]
    assert claim_data["lease_expires_at"] is not None
    assert claim_data["task_id"] == task_id
    assert claim_data["task_sha256"] == "sha256-task-for-serde"

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "running"


def test_claim_requires_jobs_and_max_jobs_payload(client):
    token = _create_runner(client, "runner-claim-payload-required")

    response = client.post(
        "/api/runners/runner-claim-payload-required/claim",
        headers=_runner_headers(token),
        json={"runner_id": "runner-claim-payload-required"},
    )

    assert response.status_code == 422
    missing_fields = {tuple(item["loc"]) for item in response.json().get("detail", [])}
    assert ("body", "jobs") in missing_fields
    assert ("body", "max_jobs") in missing_fields


def test_claim_returns_204_when_runner_reports_capacity_full(client):
    token = _create_runner(client, "runner-claim-capacity-full")
    job_id = _create_job(client, client.app)

    claim_response = client.post(
        "/api/runners/runner-claim-capacity-full/claim",
        headers=_runner_headers(token),
        json={"jobs": 1, "max_jobs": 1},
    )

    assert claim_response.status_code == 204
    assert claim_response.text == ""

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "pending"


def test_claim_rejects_jobs_greater_than_max_jobs(client):
    token = _create_runner(client, "runner-claim-invalid-jobs")

    response = client.post(
        "/api/runners/runner-claim-invalid-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": 2, "max_jobs": 1},
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Invalid claim payload: jobs cannot exceed max_jobs"
    )


def test_claim_rejects_max_jobs_over_hard_limit(client):
    token = _create_runner(client, "runner-claim-overflow-max-jobs")

    response = client.post(
        "/api/runners/runner-claim-overflow-max-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 257},
    )

    assert response.status_code == 422
    assert (
        response.json()["detail"]
        == "Invalid claim payload: max_jobs exceeds hard limit"
    )


def test_claim_rejects_negative_jobs(client):
    token = _create_runner(client, "runner-claim-negative-jobs")

    response = client.post(
        "/api/runners/runner-claim-negative-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": -1, "max_jobs": 1},
    )

    assert response.status_code == 422


def test_claim_rejects_zero_max_jobs(client):
    token = _create_runner(client, "runner-claim-zero-max-jobs")

    response = client.post(
        "/api/runners/runner-claim-zero-max-jobs/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 0},
    )

    assert response.status_code == 422


def test_claim_ignores_body_runner_id_and_uses_path_runner_id(client):
    token = _create_runner(client, "runner-claim-path-authoritative")
    _create_runner(client, "runner-body-ignored")

    _create_job(client, client.app)

    claim_response = client.post(
        "/api/runners/runner-claim-path-authoritative/claim",
        headers=_runner_headers(token),
        json={"runner_id": "runner-body-ignored", "jobs": 0, "max_jobs": 1},
    )

    assert claim_response.status_code == 200
    assert claim_response.json()["runner_id"] == "runner-claim-path-authoritative"


def test_sync_endpoint_persists_status_and_message(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-counts-1")

    response = client.post(
        f"/api/runners/runner-counts-1/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 1,
            "status": "completed",
            "exit_code": 0,
            "message": "All tests passed",
        },
    )
    assert response.status_code == 200

    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    data = job_response.json()
    assert data["status"] == "completed"
    assert data["exit_code"] == 0
    assert data["message"] == "All tests passed"


def test_sync_endpoint_is_idempotent_for_duplicate_sync_seq(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-events-1")

    started_response = client.post(
        f"/api/runners/runner-events-1/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )
    assert started_response.status_code == 200

    completed_response = client.post(
        f"/api/runners/runner-events-1/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 2,
            "status": "completed",
        },
    )
    assert completed_response.status_code == 200

    before_duplicate = client.get(f"/api/jobs/{job_id}")
    assert before_duplicate.status_code == 200
    before_data = before_duplicate.json()
    assert before_data["status"] == "completed"
    assert before_data["finished_at"] is not None

    duplicate_response = client.post(
        f"/api/runners/runner-events-1/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={
            "lease_token": lease_token,
            "sync_seq": 2,
            "status": "failed",
        },
    )
    assert duplicate_response.status_code == 200

    after_duplicate = client.get(f"/api/jobs/{job_id}")
    assert after_duplicate.status_code == 200
    after_data = after_duplicate.json()
    assert after_data["status"] == "completed"
    assert after_data["finished_at"] == before_data["finished_at"]


def test_files_append_dedupes_chunk_seq_and_writes_once(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-dedup")

    first_chunk = _push_file(
        client,
        "runner-files-dedup",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="hello\n",
        chunk_seq=1,
    )
    assert first_chunk.status_code == 200
    assert first_chunk.json() == {"applied": True}

    duplicate_chunk = _push_file(
        client,
        "runner-files-dedup",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="ignored\n",
        chunk_seq=1,
    )
    assert duplicate_chunk.status_code == 200
    assert duplicate_chunk.json() == {"applied": False}

    content = (_files_dir(config, job_id) / "stdout.log").read_text()
    assert content == "hello\n"


def test_files_append_applies_increasing_chunk_seq_in_order(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-order")

    for seq, text in ((1, "first\n"), (2, "second\n"), (3, "third\n")):
        response = _push_file(
            client,
            "runner-files-order",
            token,
            job_id,
            lease_token=lease_token,
            path="stdout.log",
            mode="append",
            content=text,
            chunk_seq=seq,
        )
        assert response.status_code == 200
        assert response.json() == {"applied": True}

    content = (_files_dir(config, job_id) / "stdout.log").read_text()
    assert content == "first\nsecond\nthird\n"


def test_files_append_rejects_out_of_order_chunk_seq(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-oos")

    newer = _push_file(
        client,
        "runner-files-oos",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="newer\n",
        chunk_seq=2,
    )
    assert newer.status_code == 200
    assert newer.json() == {"applied": True}

    stale = _push_file(
        client,
        "runner-files-oos",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="stale\n",
        chunk_seq=1,
    )
    assert stale.status_code == 200
    assert stale.json() == {"applied": False}

    content = (_files_dir(config, job_id) / "stdout.log").read_text()
    assert content == "newer\n"


def test_files_append_requires_chunk_seq(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-noseq")

    response = _push_file(
        client,
        "runner-files-noseq",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="hello\n",
    )

    assert response.status_code == 422


def test_files_append_supports_nested_paths(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-nested")

    response = _push_file(
        client,
        "runner-files-nested",
        token,
        job_id,
        lease_token=lease_token,
        path="logs/deep/out.log",
        mode="append",
        content="nested\n",
        chunk_seq=1,
    )
    assert response.status_code == 200
    assert response.json() == {"applied": True}

    content = (_files_dir(config, job_id) / "logs" / "deep" / "out.log").read_text()
    assert content == "nested\n"


def test_files_full_overwrites_previous_content(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-full")

    first_upload = _push_file(
        client,
        "runner-files-full",
        token,
        job_id,
        lease_token=lease_token,
        path="watch/status.txt",
        mode="full",
        content="first\n",
    )
    assert first_upload.status_code == 200
    assert first_upload.json() == {"applied": True}

    second_upload = _push_file(
        client,
        "runner-files-full",
        token,
        job_id,
        lease_token=lease_token,
        path="watch/status.txt",
        mode="full",
        content="second\n",
    )
    assert second_upload.status_code == 200
    assert second_upload.json() == {"applied": True}

    content = (_files_dir(config, job_id) / "watch" / "status.txt").read_text()
    assert content == "second\n"


def test_files_full_truncates_to_configured_cap(small_caps_client):
    config, client = small_caps_client
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-cap")

    over_cap = "x" * (1024 * 1024) + "tail-marker"
    response = _push_file(
        client,
        "runner-files-cap",
        token,
        job_id,
        lease_token=lease_token,
        path="watch/big.txt",
        mode="full",
        content=over_cap,
    )
    assert response.status_code == 200
    assert response.json() == {"applied": True}

    content = (_files_dir(config, job_id) / "watch" / "big.txt").read_text()
    assert len(content.encode("utf-8")) == 1024 * 1024
    assert content.endswith("tail-marker")
    assert content == over_cap[-(1024 * 1024) :]


def test_files_push_rejects_path_traversal(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-files-traversal")

    for bad_path in ("../evil.log", "/etc/passwd", "a/../../b.log"):
        response = _push_file(
            client,
            "runner-files-traversal",
            token,
            job_id,
            lease_token=lease_token,
            path=bad_path,
            mode="append",
            content="nope\n",
            chunk_seq=1,
        )
        assert response.status_code == 400, bad_path


def test_files_push_rejects_wrong_lease(client):
    job_id, token, _lease_token = _create_and_claim_job(client, "runner-files-lease")

    response = _push_file(
        client,
        "runner-files-lease",
        token,
        job_id,
        lease_token="wrong-lease",
        path="stdout.log",
        mode="append",
        content="hello\n",
        chunk_seq=1,
    )

    assert response.status_code == 409


def test_files_push_rejects_wrong_runner_token(client):
    job_id, _token, lease_token = _create_and_claim_job(client, "runner-files-auth")

    response = _push_file(
        client,
        "runner-files-auth",
        "wrong-token",
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="hello\n",
        chunk_seq=1,
    )

    assert response.status_code == 403


def test_old_log_push_endpoints_are_removed(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-old-logs")

    chunk_response = client.post(
        f"/api/runners/runner-old-logs/jobs/{job_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "hello\n"},
    )
    assert chunk_response.status_code == 404

    full_response = client.post(
        f"/api/runners/runner-old-logs/jobs/{job_id}/logs/stdout",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "content": "hello\n"},
    )
    assert full_response.status_code == 404


def test_task_zip_download_returns_exact_bytes(client):
    token = _create_runner(client, "runner-zip-download")
    toml = (
        b'name = "zip-task"\nimage = "img:latest"\n'
        b'cmd = "echo hi"\nworkspace = "/workspace"\n'
    )
    zip_bytes = _build_task_zip(toml, extra={"run.sh": "echo hi\n"})
    task_id = _upload_task(client, zip_bytes)

    response = client.get(
        f"/api/runners/runner-zip-download/tasks/{task_id}/zip",
        headers=_runner_headers(token),
    )

    assert response.status_code == 200
    assert response.content == zip_bytes


def test_task_zip_download_unknown_task_returns_404(client):
    token = _create_runner(client, "runner-zip-missing")

    response = client.get(
        "/api/runners/runner-zip-missing/tasks/99999/zip",
        headers=_runner_headers(token),
    )

    assert response.status_code == 404


def test_task_zip_download_requires_runner_auth(client):
    toml = (
        b'name = "zip-auth-task"\nimage = "img:latest"\n'
        b'cmd = "echo hi"\nworkspace = "/workspace"\n'
    )
    task_id = _upload_task(client, _build_task_zip(toml))

    no_token = client.get(f"/api/runners/runner-zip-auth/tasks/{task_id}/zip")
    assert no_token.status_code == 403

    _create_runner(client, "runner-zip-auth")
    bad_token = client.get(
        f"/api/runners/runner-zip-auth/tasks/{task_id}/zip",
        headers=_runner_headers("wrong-token"),
    )
    assert bad_token.status_code == 403


def test_artifacts_upload_saves_file_bytes(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-artifacts-1")

    payload = b"\x00\x01binary-results\xff" * 64
    response = client.post(
        f"/api/runners/runner-artifacts-1/jobs/{job_id}/artifacts",
        headers=_runner_headers(token),
        data={"lease_token": lease_token, "path": "results/out.bin"},
        files={"file": ("out.bin", payload, "application/octet-stream")},
    )

    assert response.status_code == 200
    saved = _artifacts_dir(config, job_id) / "results" / "out.bin"
    assert saved.read_bytes() == payload


def test_artifacts_upload_over_cap_returns_413_and_removes_partial(small_caps_client):
    config, client = small_caps_client
    job_id, token, lease_token = _create_and_claim_job(client, "runner-artifacts-cap")

    over_cap = b"x" * (1024 * 1024 + 1)
    response = client.post(
        f"/api/runners/runner-artifacts-cap/jobs/{job_id}/artifacts",
        headers=_runner_headers(token),
        data={"lease_token": lease_token, "path": "results/big.bin"},
        files={"file": ("big.bin", over_cap, "application/octet-stream")},
    )

    assert response.status_code == 413
    assert not (_artifacts_dir(config, job_id) / "results" / "big.bin").exists()


def test_artifacts_upload_rejects_path_traversal(client, config):
    job_id, token, lease_token = _create_and_claim_job(
        client, "runner-artifacts-traversal"
    )

    for bad_path in ("../evil.bin", "/etc/passwd"):
        response = client.post(
            f"/api/runners/runner-artifacts-traversal/jobs/{job_id}/artifacts",
            headers=_runner_headers(token),
            data={"lease_token": lease_token, "path": bad_path},
            files={"file": ("evil.bin", b"nope", "application/octet-stream")},
        )
        assert response.status_code == 400, bad_path

    assert not (_artifacts_dir(config, job_id).exists())


def test_artifacts_upload_rejects_wrong_lease(client):
    job_id, token, _lease_token = _create_and_claim_job(
        client, "runner-artifacts-lease"
    )

    response = client.post(
        f"/api/runners/runner-artifacts-lease/jobs/{job_id}/artifacts",
        headers=_runner_headers(token),
        data={"lease_token": "wrong-lease", "path": "results/out.bin"},
        files={"file": ("out.bin", b"data", "application/octet-stream")},
    )

    assert response.status_code == 409


def test_retry_clears_old_files_and_allows_chunk_seq_restart(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-retry-files")

    first_chunk = _push_file(
        client,
        "runner-retry-files",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="old\n",
        chunk_seq=1,
    )
    assert first_chunk.status_code == 200
    assert first_chunk.json() == {"applied": True}

    complete_response = client.post(
        f"/api/runners/runner-retry-files/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "completed"},
    )
    assert complete_response.status_code == 200

    retry_response = client.post(f"/api/jobs/{job_id}/retry")
    assert retry_response.status_code == 200
    assert not (_files_dir(config, job_id) / "stdout.log").exists()

    second_claim = client.post(
        "/api/runners/runner-retry-files/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert second_claim.status_code == 200
    second_lease_token = second_claim.json()["lease_token"]

    second_chunk = _push_file(
        client,
        "runner-retry-files",
        token,
        job_id,
        lease_token=second_lease_token,
        path="stdout.log",
        mode="append",
        content="new\n",
        chunk_seq=1,
    )
    assert second_chunk.status_code == 200
    assert second_chunk.json() == {"applied": True}

    content = (_files_dir(config, job_id) / "stdout.log").read_text()
    assert content == "new\n"


def test_claim_clears_stale_files_and_artifacts_before_new_attempt(client, config):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-claim-clear")

    first_chunk = _push_file(
        client,
        "runner-claim-clear",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="old\n",
        chunk_seq=1,
    )
    assert first_chunk.status_code == 200
    assert first_chunk.json() == {"applied": True}

    artifact_response = client.post(
        f"/api/runners/runner-claim-clear/jobs/{job_id}/artifacts",
        headers=_runner_headers(token),
        data={"lease_token": lease_token, "path": "results/out.bin"},
        files={"file": ("out.bin", b"old-artifact", "application/octet-stream")},
    )
    assert artifact_response.status_code == 200

    complete_response = client.post(
        f"/api/runners/runner-claim-clear/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "completed"},
    )
    assert complete_response.status_code == 200

    retry_response = client.post(f"/api/jobs/{job_id}/retry")
    assert retry_response.status_code == 200

    # Simulate stale content left behind before runner re-claims job.
    stale_log_path = _files_dir(config, job_id) / "stdout.log"
    stale_log_path.parent.mkdir(parents=True, exist_ok=True)
    stale_log_path.write_text("stale-before-claim\n")
    stale_artifact_dir = _artifacts_dir(config, job_id)
    stale_artifact_dir.mkdir(parents=True, exist_ok=True)
    (stale_artifact_dir / "stale.bin").write_bytes(b"stale")

    second_claim = client.post(
        "/api/runners/runner-claim-clear/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert second_claim.status_code == 200

    # Claim-time cleanup should remove stale files and artifacts immediately.
    assert not stale_log_path.exists()
    assert not (stale_artifact_dir / "stale.bin").exists()


def test_sync_endpoint_returns_409_for_lease_mismatch(client):
    job_id, token, _lease_token = _create_and_claim_job(client, "runner-lease-1")

    response = client.post(
        f"/api/runners/runner-lease-1/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": "wrong-lease", "sync_seq": 1, "status": "running"},
    )

    assert response.status_code == 409


def test_heartbeat_extends_job_lease(short_lease_client):
    """Regression test: heartbeat must extend RUNNING job leases to prevent 409 on file pushes."""
    _cfg, app, client = short_lease_client
    job_id, token, lease_token = _create_and_claim_job(client, "runner-lease-hb")

    # Wait long enough that the original 1-second lease would have expired
    time.sleep(1.5)

    # Send heartbeat, which should extend the lease
    heartbeat_response = client.post(
        "/api/runners/runner-lease-hb/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat_response.status_code == 200

    # Run reconciliation: job should NOT be reset because lease was extended
    app.state.scheduler.reconcile_expired_leases()
    job_response = client.get(f"/api/jobs/{job_id}")
    assert job_response.status_code == 200
    job_data = job_response.json()
    assert job_data["status"] == "running"

    # File chunk push should succeed without 409
    chunk_response = _push_file(
        client,
        "runner-lease-hb",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="hello\n",
        chunk_seq=1,
    )
    assert chunk_response.status_code == 200


def test_reclaimed_job_accepts_chunk_seq_restart(short_lease_client):
    cfg, app, client = short_lease_client
    job_id, token, lease_token = _create_and_claim_job(
        client, "runner-reclaim-file-seq"
    )

    first_chunk = _push_file(
        client,
        "runner-reclaim-file-seq",
        token,
        job_id,
        lease_token=lease_token,
        path="stdout.log",
        mode="append",
        content="first-attempt\n",
        chunk_seq=1,
    )
    assert first_chunk.status_code == 200

    _reconcile_until_pending(app, client, job_id)

    second_claim = client.post(
        "/api/runners/runner-reclaim-file-seq/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert second_claim.status_code == 200
    second_lease_token = second_claim.json()["lease_token"]

    second_chunk = _push_file(
        client,
        "runner-reclaim-file-seq",
        token,
        job_id,
        lease_token=second_lease_token,
        path="stdout.log",
        mode="append",
        content="second-attempt\n",
        chunk_seq=1,
    )
    assert second_chunk.status_code == 200
    assert second_chunk.json() == {"applied": True}

    content = (_files_dir(cfg, job_id) / "stdout.log").read_text()
    assert content == "second-attempt\n"


def test_runner_metrics_endpoint_accepts_valid_payload(client):
    token = _create_runner(client, "runner-metrics-1")
    response = client.post(
        "/api/runners/runner-metrics-1/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 12.5,
            "memory_percent": 48.0,
            "disk_percent": 66.1,
            "active_tasks": 1,
        },
    )
    assert response.status_code == 200


def test_runner_metrics_rejects_invalid_ranges(client):
    token = _create_runner(client, "runner-metrics-range")
    response = client.post(
        "/api/runners/runner-metrics-range/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 120,
            "memory_percent": 10,
            "disk_percent": 10,
            "active_tasks": 0,
        },
    )
    assert response.status_code == 422


def test_runner_metrics_rejects_negative_active_tasks(client):
    token = _create_runner(client, "runner-metrics-negative")
    response = client.post(
        "/api/runners/runner-metrics-negative/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 10,
            "memory_percent": 10,
            "disk_percent": 10,
            "active_tasks": -1,
        },
    )
    assert response.status_code == 422


def test_runner_metrics_rejects_invalid_token(client):
    _create_runner(client, "runner-metrics-2")
    response = client.post(
        "/api/runners/runner-metrics-2/metrics",
        headers=_runner_headers("wrong-token"),
        json={
            "cpu_percent": 1,
            "memory_percent": 2,
            "disk_percent": 3,
            "active_tasks": 0,
        },
    )
    assert response.status_code == 403


def test_runner_metrics_missing_timestamp_falls_back_to_server_time(client):
    token = _create_runner(client, "runner-time-missing")
    post_response = client.post(
        "/api/runners/runner-time-missing/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 11,
            "memory_percent": 22,
            "disk_percent": 33,
            "active_tasks": 0,
        },
    )
    assert post_response.status_code == 200

    query_response = client.get(
        "/api/admin/runners/runner-time-missing/metrics",
        headers=_admin_headers(),
        params={"window": "1h"},
    )
    assert query_response.status_code == 200
    assert len(query_response.json()["series"]) >= 1


def test_runner_metrics_invalid_timestamp_falls_back_to_server_time(client):
    token = _create_runner(client, "runner-time-invalid")
    post_response = client.post(
        "/api/runners/runner-time-invalid/metrics",
        headers=_runner_headers(token),
        json={
            "timestamp": "not-a-datetime",
            "cpu_percent": 9,
            "memory_percent": 9,
            "disk_percent": 9,
            "active_tasks": 0,
        },
    )
    assert post_response.status_code == 200

    query_response = client.get(
        "/api/admin/runners/runner-time-invalid/metrics",
        headers=_admin_headers(),
        params={"window": "1h"},
    )
    assert query_response.status_code == 200
    assert len(query_response.json()["series"]) >= 1


def test_admin_overview_requires_admin_token(client):
    _create_runner(client, "runner-overview-auth")
    response = client.get(
        "/api/admin/runners/overview",
        headers={"X-Admin-Token": "wrong-token"},
    )
    assert response.status_code == 403


def test_admin_overview_returns_health_and_latest_metrics(client):
    token = _create_runner(client, "runner-overview-1")
    metrics_resp = client.post(
        "/api/runners/runner-overview-1/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 20,
            "memory_percent": 30,
            "disk_percent": 40,
            "active_tasks": 0,
        },
    )
    assert metrics_resp.status_code == 200

    heartbeat = client.post(
        "/api/runners/runner-overview-1/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat.status_code == 200

    response = client.get("/api/admin/runners/overview", headers=_admin_headers())
    assert response.status_code == 200
    items = response.json()
    target = next(i for i in items if i["runner_id"] == "runner-overview-1")
    assert target["health_status"] == "online"
    assert target["latest_metrics"]["cpu_percent"] == 20


def test_admin_overview_marks_disabled_runner_as_disabled(client):
    _create_runner(client, "runner-disabled-1")
    disable_resp = client.post(
        "/api/admin/runners/runner-disabled-1/disable", headers=_admin_headers()
    )
    assert disable_resp.status_code == 200

    response = client.get("/api/admin/runners/overview", headers=_admin_headers())
    target = next(i for i in response.json() if i["runner_id"] == "runner-disabled-1")
    assert target["health_status"] == "disabled"
    assert target["health_status"] != "idle"


def test_admin_overview_marks_no_heartbeat_runner_as_offline(client):
    _create_runner(client, "runner-offline-1")
    response = client.get("/api/admin/runners/overview", headers=_admin_headers())
    target = next(i for i in response.json() if i["runner_id"] == "runner-offline-1")
    assert target["health_status"] == "offline"


def test_admin_runner_metrics_requires_admin_token(client):
    _create_runner(client, "runner-window-auth")
    response = client.get(
        "/api/admin/runners/runner-window-auth/metrics",
        headers={"X-Admin-Token": "wrong-token"},
        params={"window": "1h"},
    )
    assert response.status_code == 403


def test_admin_runner_metrics_returns_windowed_series_and_latest(client):
    token = _create_runner(client, "runner-window-1")
    post_response = client.post(
        "/api/runners/runner-window-1/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 10,
            "memory_percent": 20,
            "disk_percent": 30,
            "active_tasks": 1,
        },
    )
    assert post_response.status_code == 200

    response = client.get(
        "/api/admin/runners/runner-window-1/metrics",
        headers=_admin_headers(),
        params={"window": "1h"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "runner" in data
    assert data["window"] == "1h"
    assert "latest" in data
    assert "series" in data
    assert data["runner"]["runner_id"] == "runner-window-1"
    assert "enabled" in data["runner"]
    assert "last_seen_at" in data["runner"]
    assert data["runner"]["health_status"] in ("online", "offline", "disabled")
    assert len(data["series"]) >= 1
    timestamps = [item["timestamp"] for item in data["series"]]
    assert timestamps == sorted(timestamps)


def test_admin_runner_metrics_supports_6h_and_24h_windows(client):
    token = _create_runner(client, "runner-window-2")
    post_response = client.post(
        "/api/runners/runner-window-2/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 5,
            "memory_percent": 5,
            "disk_percent": 5,
            "active_tasks": 0,
        },
    )
    assert post_response.status_code == 200

    for window in ("6h", "24h"):
        response = client.get(
            f"/api/admin/runners/runner-window-2/metrics",
            headers=_admin_headers(),
            params={"window": window},
        )
        assert response.status_code == 200
        assert response.json()["window"] == window


def test_admin_runner_metrics_defaults_window_to_1h(client):
    token = _create_runner(client, "runner-window-default")
    client.post(
        "/api/runners/runner-window-default/metrics",
        headers=_runner_headers(token),
        json={
            "cpu_percent": 7,
            "memory_percent": 8,
            "disk_percent": 9,
            "active_tasks": 0,
        },
    )

    response = client.get(
        "/api/admin/runners/runner-window-default/metrics",
        headers=_admin_headers(),
    )
    assert response.status_code == 200
    assert response.json()["window"] == "1h"


def test_admin_runner_metrics_rejects_invalid_window(client):
    _create_runner(client, "runner-window-invalid")
    response = client.get(
        "/api/admin/runners/runner-window-invalid/metrics",
        headers=_admin_headers(),
        params={"window": "2h"},
    )
    assert response.status_code == 422


def test_admin_runner_metrics_returns_empty_series_without_metrics(client):
    _create_runner(client, "runner-window-empty")
    response = client.get(
        "/api/admin/runners/runner-window-empty/metrics",
        headers=_admin_headers(),
        params={"window": "1h"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["series"] == []
    assert data["latest"] is None


def test_disabled_runner_rejected_by_runner_endpoints(client):
    token = _create_runner(client, "runner-disabled-auth")

    disable_response = client.post(
        "/api/admin/runners/runner-disabled-auth/disable",
        headers=_admin_headers(),
    )
    assert disable_response.status_code == 200

    heartbeat_response = client.post(
        "/api/runners/runner-disabled-auth/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat_response.status_code == 403

    claim_response = client.post(
        "/api/runners/runner-disabled-auth/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert claim_response.status_code == 403


def test_deleted_runner_rejected_by_runner_endpoints(client):
    token = _create_runner(client, "runner-deleted-auth")

    delete_response = client.delete(
        "/api/admin/runners/runner-deleted-auth",
        headers=_admin_headers(),
    )
    assert delete_response.status_code == 200

    heartbeat_response = client.post(
        "/api/runners/runner-deleted-auth/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat_response.status_code == 403

    claim_response = client.post(
        "/api/runners/runner-deleted-auth/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert claim_response.status_code == 403


def test_enable_restores_runner_auth(client):
    token = _create_runner(client, "runner-enable-auth")

    disable_response = client.post(
        "/api/admin/runners/runner-enable-auth/disable",
        headers=_admin_headers(),
    )
    assert disable_response.status_code == 200

    enable_response = client.post(
        "/api/admin/runners/runner-enable-auth/enable",
        headers=_admin_headers(),
    )
    assert enable_response.status_code == 200

    heartbeat_response = client.post(
        "/api/runners/runner-enable-auth/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat_response.status_code == 200


@pytest.mark.parametrize(
    ("action", "http_method", "action_path"),
    [
        ("disable", "post", "/api/admin/runners/{runner_id}/disable"),
        ("delete", "delete", "/api/admin/runners/{runner_id}"),
    ],
)
def test_action_after_claim_requeues_job_after_lease_expiry(
    short_lease_client,
    action,
    http_method,
    action_path,
):
    _cfg, app, client = short_lease_client
    runner_id = f"runner-{action}-lease"
    job_id, _token, _lease_token = _create_and_claim_job(client, runner_id)

    action_response = getattr(client, http_method)(
        action_path.format(runner_id=runner_id),
        headers=_admin_headers(),
    )
    assert action_response.status_code == 200

    before_expiry = client.get(f"/api/jobs/{job_id}")
    assert before_expiry.status_code == 200
    assert before_expiry.json()["status"] == "running"

    job_data = _reconcile_until_pending(app, client, job_id)
    assert job_data["runner_id"] is None
    assert job_data.get("lease_token") is None
    assert job_data.get("lease_expires_at") is None


def test_job_sync_endpoint_accepts_valid_lease(client):
    job_id, token, lease_token = _create_and_claim_job(client, "runner-sync-valid")

    response = client.post(
        f"/api/runners/runner-sync-valid/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "sync_seq": 1, "status": "running"},
    )
    assert response.status_code == 200
    assert response.json()["synced"] is True


def test_job_sync_endpoint_rejects_invalid_lease(client):
    job_id, token, _lease_token = _create_and_claim_job(client, "runner-sync-invalid")

    response = client.post(
        f"/api/runners/runner-sync-invalid/jobs/{job_id}/sync",
        headers=_runner_headers(token),
        json={"lease_token": "bad-token", "sync_seq": 1, "status": "running"},
    )
    assert response.status_code == 409
