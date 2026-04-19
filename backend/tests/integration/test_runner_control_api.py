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
        yield short_lease_app, short_lease_test_client


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
        json={"jobs": 0, "max_jobs": 1},
    )
    assert claim_response.status_code == 200
    lease_token = claim_response.json()["lease_token"]
    return task_id, token, lease_token


def _reconcile_until_pending(
    app,
    client: TestClient,
    task_id: int,
    timeout_seconds: float = 5.0,
    interval_seconds: float = 0.1,
):
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        app.state.scheduler.reconcile_expired_leases()
        response = client.get(f"/api/tasks/{task_id}")
        assert response.status_code == 200
        task_data = response.json()
        if task_data["status"] == "pending":
            return task_data
        time.sleep(interval_seconds)

    pytest.fail(f"Task {task_id} did not return to pending before timeout")


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
        json={"jobs": 0, "max_jobs": 1},
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
        json={"jobs": 0, "max_jobs": 1},
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
    create_task_response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    assert create_task_response.status_code == 200
    task_id = create_task_response.json()["task_id"]

    claim_response = client.post(
        "/api/runners/runner-claim-capacity-full/claim",
        headers=_runner_headers(token),
        json={"jobs": 1, "max_jobs": 1},
    )

    assert claim_response.status_code == 204
    assert claim_response.text == ""

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    assert task_response.json()["status"] == "pending"


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

    create_task_response = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    assert create_task_response.status_code == 200

    claim_response = client.post(
        "/api/runners/runner-claim-path-authoritative/claim",
        headers=_runner_headers(token),
        json={"runner_id": "runner-body-ignored", "jobs": 0, "max_jobs": 1},
    )

    assert claim_response.status_code == 200
    assert claim_response.json()["runner_id"] == "runner-claim-path-authoritative"


def test_events_endpoint_persists_runner_counts_and_message(client):
    task_id, token, lease_token = _create_and_claim_task(client, "runner-counts-1")

    response = client.post(
        f"/api/runners/runner-counts-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={
            "lease_token": lease_token,
            "event_seq": 1,
            "event_type": "completed",
            "exit_code": 0,
            "message": "All tests passed",
            "case_count": 42,
            "poc_count": 7,
            "compile_failed": 3,
        },
    )
    assert response.status_code == 200

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    data = task_response.json()
    assert data["status"] == "completed"
    assert data["exit_code"] == 0
    assert data["message"] == "All tests passed"
    assert data["case_count"] == 42
    assert data["poc_count"] == 7
    assert data["compile_failed"] == 3


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
        json={
            "lease_token": lease_token,
            "event_seq": 2,
            "event_type": "completed",
            "case_count": 10,
            "poc_count": 2,
        },
    )
    assert completed_response.status_code == 200

    before_duplicate = client.get(f"/api/tasks/{task_id}")
    assert before_duplicate.status_code == 200
    before_data = before_duplicate.json()
    assert before_data["status"] == "completed"
    assert before_data["finished_at"] is not None
    assert before_data["case_count"] == 10

    duplicate_response = client.post(
        f"/api/runners/runner-events-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={
            "lease_token": lease_token,
            "event_seq": 2,
            "event_type": "failed",
            "case_count": 99,
        },
    )
    assert duplicate_response.status_code == 200

    after_duplicate = client.get(f"/api/tasks/{task_id}")
    assert after_duplicate.status_code == 200
    after_data = after_duplicate.json()
    assert after_data["status"] == "completed"
    assert after_data["finished_at"] == before_data["finished_at"]
    assert after_data["case_count"] == 10


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


def test_retry_clears_old_logs_and_allows_chunk_seq_restart(client):
    task_id, token, lease_token = _create_and_claim_task(client, "runner-retry-logs")

    first_chunk = client.post(
        f"/api/runners/runner-retry-logs/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "old\n"},
    )
    assert first_chunk.status_code == 200

    complete_response = client.post(
        f"/api/runners/runner-retry-logs/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "event_seq": 1, "event_type": "completed"},
    )
    assert complete_response.status_code == 200

    retry_response = client.post(f"/api/tasks/{task_id}/retry")
    assert retry_response.status_code == 200

    second_claim = client.post(
        "/api/runners/runner-retry-logs/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert second_claim.status_code == 200
    second_lease_token = second_claim.json()["lease_token"]

    second_chunk = client.post(
        f"/api/runners/runner-retry-logs/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": second_lease_token, "chunk_seq": 1, "content": "new\n"},
    )
    assert second_chunk.status_code == 200

    raw_log = client.get(f"/api/tasks/{task_id}/logs/stdout/raw")
    assert raw_log.status_code == 200
    assert raw_log.text == "new\n"


def test_claim_clears_stale_logs_before_new_attempt(client, config):
    task_id, token, lease_token = _create_and_claim_task(client, "runner-claim-clear")

    first_chunk = client.post(
        f"/api/runners/runner-claim-clear/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "old\n"},
    )
    assert first_chunk.status_code == 200

    complete_response = client.post(
        f"/api/runners/runner-claim-clear/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "event_seq": 1, "event_type": "completed"},
    )
    assert complete_response.status_code == 200

    retry_response = client.post(f"/api/tasks/{task_id}/retry")
    assert retry_response.status_code == 200

    # Simulate stale content left behind before runner re-claims task.
    stale_log_path = config.workspace_path / "logs" / "serde-1.0.0-stdout.log"
    stale_log_path.write_text("stale-before-claim\n")

    second_claim = client.post(
        "/api/runners/runner-claim-clear/claim",
        headers=_runner_headers(token),
        json={"jobs": 0, "max_jobs": 1},
    )
    assert second_claim.status_code == 200

    # Claim-time cleanup should remove stale content immediately.
    raw_log = client.get(f"/api/tasks/{task_id}/logs/stdout/raw")
    assert raw_log.status_code == 404


def test_events_endpoint_returns_409_for_lease_mismatch(client):
    task_id, token, _lease_token = _create_and_claim_task(client, "runner-lease-1")

    response = client.post(
        f"/api/runners/runner-lease-1/tasks/{task_id}/events",
        headers=_runner_headers(token),
        json={"lease_token": "wrong-lease", "event_seq": 1, "event_type": "started"},
    )

    assert response.status_code == 409


def test_heartbeat_extends_task_lease(short_lease_client):
    """Regression test: heartbeat must extend RUNNING task leases to prevent 409 on log uploads."""
    app, client = short_lease_client
    task_id, token, lease_token = _create_and_claim_task(client, "runner-lease-hb")

    # Wait long enough that the original 1-second lease would have expired
    time.sleep(1.5)

    # Send heartbeat, which should extend the lease
    heartbeat_response = client.post(
        "/api/runners/runner-lease-hb/heartbeat",
        headers=_runner_headers(token),
    )
    assert heartbeat_response.status_code == 200

    # Run reconciliation: task should NOT be reset because lease was extended
    app.state.scheduler.reconcile_expired_leases()
    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "running"

    # Log chunk upload should succeed without 409
    chunk_response = client.post(
        f"/api/runners/runner-lease-hb/tasks/{task_id}/logs/stdout/chunks",
        headers=_runner_headers(token),
        json={"lease_token": lease_token, "chunk_seq": 1, "content": "hello\n"},
    )
    assert chunk_response.status_code == 200


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
            "/api/admin/runners/runner-window-2/metrics",
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
def test_action_after_claim_requeues_task_after_lease_expiry(
    short_lease_client,
    action,
    http_method,
    action_path,
):
    app, client = short_lease_client
    runner_id = f"runner-{action}-lease"
    task_id, _token, _lease_token = _create_and_claim_task(client, runner_id)

    action_response = getattr(client, http_method)(
        action_path.format(runner_id=runner_id),
        headers=_admin_headers(),
    )
    assert action_response.status_code == 200

    before_expiry = client.get(f"/api/tasks/{task_id}")
    assert before_expiry.status_code == 200
    assert before_expiry.json()["status"] == "running"

    task_data = _reconcile_until_pending(app, client, task_id)
    assert task_data["runner_id"] is None
    assert task_data.get("lease_token") is None
    assert task_data.get("lease_expires_at") is None
