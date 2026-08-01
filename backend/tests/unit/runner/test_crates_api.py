import httpx
import pytest

from runner.cache import TTLCache
from runner.crates_api import CrateNotFoundError, CratesAPI
from runner.rate_limiter import AsyncTokenBucket


def _client_with_transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_latest_version_returns_max_version():
    def handler(request: httpx.Request):
        assert request.headers["user-agent"] == "crateprobe-runner"
        return httpx.Response(
            200,
            json={"crate": {"max_version": "1.2.3"}, "versions": []},
        )

    api = CratesAPI(client=_client_with_transport(handler))
    version = await api.get_latest_version("serde")
    assert version == "1.2.3"
    await api.close()


@pytest.mark.asyncio
async def test_get_latest_version_raises_on_404():
    def handler(request: httpx.Request):
        return httpx.Response(404)

    api = CratesAPI(client=_client_with_transport(handler))
    with pytest.raises(CrateNotFoundError):
        await api.get_latest_version("does-not-exist")
    await api.close()


@pytest.mark.asyncio
async def test_cache_avoids_duplicate_requests():
    calls = []

    def handler(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"crate": {"max_version": "1.2.3"}, "versions": []},
        )

    cache = TTLCache(ttl_seconds=60.0)
    api = CratesAPI(
        client=_client_with_transport(handler),
        cache=cache,
        rate_limiter=AsyncTokenBucket(rate=1000.0),
    )
    await api.get_latest_version("serde")
    await api.get_latest_version("serde")
    assert len(calls) == 1
    await api.close()


@pytest.mark.asyncio
async def test_429_respects_retry_after(monkeypatch):
    responses = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"crate": {"max_version": "1.0.0"}, "versions": []}),
    ]

    def handler(request: httpx.Request):
        return responses.pop(0)

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    api = CratesAPI(
        client=_client_with_transport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000.0),
        cache=TTLCache(ttl_seconds=60.0),
    )
    version = await api.get_latest_version("serde")
    assert version == "1.0.0"
    assert sleeps == [1.0]
    await api.close()


@pytest.mark.asyncio
async def test_429_without_retry_after_uses_exponential_backoff(monkeypatch):
    responses = [
        httpx.Response(429),
        httpx.Response(200, json={"crate": {"max_version": "1.0.0"}, "versions": []}),
    ]

    def handler(request: httpx.Request):
        return responses.pop(0)

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    api = CratesAPI(
        client=_client_with_transport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000.0),
        cache=TTLCache(ttl_seconds=60.0),
    )
    version = await api.get_latest_version("serde")
    assert version == "1.0.0"
    assert sleeps == [1.0]
    await api.close()


@pytest.mark.asyncio
async def test_server_error_retries_then_raises(monkeypatch):
    def handler(request: httpx.Request):
        return httpx.Response(500)

    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)

    api = CratesAPI(
        client=_client_with_transport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000.0),
        cache=TTLCache(ttl_seconds=60.0),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await api.get_latest_version("serde")
    assert sleeps == [1.0, 2.0]
    await api.close()


@pytest.mark.asyncio
async def test_verify_version_exists_returns_true_when_present():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "crate": {"max_version": "1.2.3"},
                "versions": [{"num": "1.0.0"}, {"num": "1.2.3"}],
            },
        )

    api = CratesAPI(
        client=_client_with_transport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000.0),
    )
    assert await api.verify_version_exists("serde", "1.0.0") is True
    await api.close()


@pytest.mark.asyncio
async def test_verify_version_exists_returns_false_when_missing():
    def handler(request: httpx.Request):
        return httpx.Response(
            200,
            json={
                "crate": {"max_version": "1.2.3"},
                "versions": [{"num": "1.2.3"}],
            },
        )

    api = CratesAPI(
        client=_client_with_transport(handler),
        rate_limiter=AsyncTokenBucket(rate=1000.0),
    )
    assert await api.verify_version_exists("serde", "1.0.0") is False
    await api.close()
