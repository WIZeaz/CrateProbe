import json

import httpx
import pytest

from runner.client import RunnerControlClient


def _patch_async_client(monkeypatch: pytest.MonkeyPatch, handler):
    original_async_client = httpx.AsyncClient

    def factory(*, base_url, headers, timeout):
        return original_async_client(
            transport=httpx.MockTransport(handler),
            base_url=base_url,
            headers=headers,
            timeout=timeout,
        )

    monkeypatch.setattr("runner.client.httpx.AsyncClient", factory)


@pytest.mark.asyncio
async def test_claim_returns_none_on_204(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/runners/runner-1/claim"
        return httpx.Response(status_code=204, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    payload = {"runner_id": "runner-1", "jobs": 0, "max_jobs": 3}
    result = await client.claim(payload)

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_claim_sends_jobs_and_max_jobs(monkeypatch: pytest.MonkeyPatch):
    payload = {"runner_id": "runner-1", "jobs": 0, "max_jobs": 3}

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/runners/runner-1/claim"
        sent_payload = json.loads(request.content.decode())
        assert sent_payload == payload
        return httpx.Response(status_code=200, json={"ok": True}, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    result = await client.claim(payload)

    assert result == {"ok": True}
    await client.aclose()


@pytest.mark.asyncio
async def test_claim_returns_job_payload_with_new_keys(monkeypatch: pytest.MonkeyPatch):
    """claim() passes through the jobs-API response dict (job_id, not id)."""
    job_payload = {
        "job_id": 7,
        "crate_name": "serde",
        "version": "1.0.0",
        "status": "running",
        "runner_id": "runner-1",
        "lease_token": "lease-7",
        "lease_expires_at": "2026-10-21T00:00:00",
        "task_id": 3,
        "task_sha256": "abc123",
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/runners/runner-1/claim"
        return httpx.Response(status_code=200, json=job_payload, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    result = await client.claim({"runner_id": "runner-1", "jobs": 0, "max_jobs": 1})

    assert result == job_payload
    await client.aclose()


@pytest.mark.asyncio
async def test_heartbeat_sends_bearer_auth_header(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/runners/runner-2/heartbeat"
        assert request.headers["Authorization"] == "Bearer heartbeat-token"
        return httpx.Response(status_code=200, json={"success": True}, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-2",
        token="heartbeat-token",
        timeout=5.0,
    )

    result = await client.heartbeat({"status": "ok"})

    assert result == {"success": True}
    await client.aclose()


@pytest.mark.asyncio
async def test_sync_job_posts_to_jobs_endpoint_and_retries_transient_5xx(
    monkeypatch: pytest.MonkeyPatch,
):
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert request.method == "POST"
        assert request.url.path == "/api/runners/runner-3/jobs/99/sync"
        if attempts < 3:
            return httpx.Response(
                status_code=503, json={"detail": "busy"}, request=request
            )
        return httpx.Response(
            status_code=200, json={"synced": True, "last_sync_seq": 1}, request=request
        )

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-3",
        token="sync-token",
        timeout=5.0,
    )

    result = await client.sync_job(
        job_id=99,
        payload={"lease_token": "lease", "sync_seq": 1, "status": "running"},
    )

    assert attempts == 3
    assert result == {"synced": True, "last_sync_seq": 1}
    await client.aclose()


@pytest.mark.asyncio
async def test_push_file_posts_json_to_files_endpoint(monkeypatch: pytest.MonkeyPatch):
    payload = {
        "lease_token": "lease-9",
        "path": "stdout.log",
        "mode": "append",
        "content": "hello\n",
        "chunk_seq": 4,
    }

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/runners/runner-1/jobs/9/files"
        sent_payload = json.loads(request.content.decode())
        assert sent_payload == payload
        return httpx.Response(status_code=200, json={"applied": True}, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    result = await client.push_file(9, payload)

    assert result == {"applied": True}
    await client.aclose()


@pytest.mark.asyncio
async def test_download_task_zip_returns_bytes(monkeypatch: pytest.MonkeyPatch):
    zip_bytes = b"PK\x03\x04fake-zip-bytes"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/api/runners/runner-1/tasks/3/zip"
        return httpx.Response(status_code=200, content=zip_bytes, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    result = await client.download_task_zip(3)

    assert result == zip_bytes
    await client.aclose()


@pytest.mark.asyncio
async def test_download_task_zip_404_raises_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=404, json={"detail": "Task not found"}, request=request
        )

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await client.download_task_zip(999)

    assert exc_info.value.response.status_code == 404
    await client.aclose()


@pytest.mark.asyncio
async def test_upload_artifact_sends_multipart_form(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    artifact = tmp_path / "results.txt"
    artifact.write_bytes(b"artifact-bytes")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/runners/runner-1/jobs/9/artifacts"
        content_type = request.headers["content-type"]
        assert content_type.startswith("multipart/form-data")
        body = request.content
        assert b'name="lease_token"' in body
        assert b"lease-9" in body
        assert b'name="path"' in body
        assert b"results/output.txt" in body
        assert b'name="file"' in body
        assert b'filename="results.txt"' in body
        assert b"artifact-bytes" in body
        return httpx.Response(
            status_code=200, json={"saved": True, "size": 14}, request=request
        )

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    result = await client.upload_artifact(9, "lease-9", "results/output.txt", artifact)

    assert result is None
    await client.aclose()


@pytest.mark.asyncio
async def test_upload_artifact_retries_transient_5xx(
    monkeypatch: pytest.MonkeyPatch, tmp_path
):
    artifact = tmp_path / "a.bin"
    artifact.write_bytes(b"x" * 8)
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        # Each retry must re-send the full file content.
        assert b"x" * 8 in request.content
        if attempts < 3:
            return httpx.Response(
                status_code=503, json={"detail": "busy"}, request=request
            )
        return httpx.Response(
            status_code=200, json={"saved": True, "size": 8}, request=request
        )

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-1",
        token="secret-token",
        timeout=5.0,
    )

    await client.upload_artifact(9, "lease-9", "a.bin", artifact)

    assert attempts == 3
    await client.aclose()


@pytest.mark.asyncio
async def test_send_metrics_posts_to_metrics_endpoint(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/runners/runner-2/metrics"
        return httpx.Response(status_code=200, json={"success": True}, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-2",
        token="metrics-token",
        timeout=5.0,
    )

    result = await client.send_metrics(
        {
            "cpu_percent": 1,
            "memory_percent": 2,
            "disk_percent": 3,
            "active_tasks": 0,
        }
    )

    assert result == {"success": True}
    await client.aclose()
