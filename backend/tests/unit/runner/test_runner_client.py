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
async def test_send_event_retries_transient_5xx(monkeypatch: pytest.MonkeyPatch):
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            return httpx.Response(
                status_code=503, json={"detail": "busy"}, request=request
            )
        return httpx.Response(status_code=200, json={"applied": True}, request=request)

    _patch_async_client(monkeypatch, handler)
    client = RunnerControlClient(
        base_url="http://control.local",
        runner_id="runner-3",
        token="event-token",
        timeout=5.0,
    )

    result = await client.send_event(
        task_id=99,
        payload={"lease_token": "lease", "event_seq": 1, "event_type": "started"},
    )

    assert attempts == 3
    assert result == {"applied": True}
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
