# crates.io API 使用政策合规 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 runner（以及 app）在从 crates.io 拉取 crate 元数据和文件时遵守 [Data Access Policy](https://crates.io/data-access)：API 调用限速 1 req/s、使用合规 User-Agent、缓存元数据、对 429/5xx 做指数退避，并把下载能力集中到共享的 `CrateDownloader`。

**Architecture:** 新增 `AsyncTokenBucket` 和 `TTLCache` 两个可测试组件；`CratesAPI` 只负责 API 元数据查询，内部挂载限速器、缓存和重试；新增 `CrateDownloader` 作为 `TaskExecutor` 的统一入口，负责版本解析和 static.crates.io 文件下载；`RunnerConfig` 通过环境变量暴露所有参数。

**Tech Stack:** Python 3.10+, FastAPI, httpx, pytest-asyncio, black.

## Global Constraints

- 对 `crates.io/api/v1/...` 的调用必须 ≤ 1 req/s。
- `User-Agent` 默认 `crateprobe-runner`，可通过 `CRATES_IO_USER_AGENT` 覆盖。
- 元数据缓存默认 TTL 300 秒，通过 `CRATES_IO_CACHE_TTL_SECONDS` 配置。
- 所有 Python 代码提交前用 `uv run black app/ tests/ runner/` 格式化。
- 单元测试使用 `httpx.MockTransport` 进行 HTTP mock，不引入额外依赖。

---

## Task 1: 为 RunnerConfig 增加 crates.io 相关环境变量

**Files:**
- Modify: `backend/runner/config.py`
- Test: `backend/tests/unit/runner/test_config.py`（新增）

**Interfaces:**
- Consumes: 无
- Produces: `RunnerConfig` 新增字段 `crates_io_user_agent`、`crates_io_rate_limit_rps`、`crates_io_cache_ttl_seconds`、`crates_io_max_concurrent_downloads`。

- [ ] **Step 1: 写失败测试**

在 `backend/tests/unit/runner/test_config.py` 中：

```python
import os
import pytest
from runner.config import RunnerConfig


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://localhost:8080")
    monkeypatch.setenv("RUNNER_ID", "test-runner")
    monkeypatch.setenv("RUNNER_TOKEN", "token")


def test_runner_config_crates_io_defaults(required_env):
    config = RunnerConfig.from_env()
    assert config.crates_io_user_agent == "crateprobe-runner"
    assert config.crates_io_rate_limit_rps == 1.0
    assert config.crates_io_cache_ttl_seconds == 300
    assert config.crates_io_max_concurrent_downloads == config.max_jobs


def test_runner_config_crates_io_from_env(required_env, monkeypatch):
    monkeypatch.setenv("CRATES_IO_USER_AGENT", "my-bot")
    monkeypatch.setenv("CRATES_IO_RATE_LIMIT_RPS", "2.5")
    monkeypatch.setenv("CRATES_IO_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("CRATES_IO_MAX_CONCURRENT_DOWNLOADS", "5")
    config = RunnerConfig.from_env()
    assert config.crates_io_user_agent == "my-bot"
    assert config.crates_io_rate_limit_rps == 2.5
    assert config.crates_io_cache_ttl_seconds == 600
    assert config.crates_io_max_concurrent_downloads == 5
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd backend
uv run pytest tests/unit/runner/test_config.py -v
```

Expected: 4 errors/失败，字段不存在。

- [ ] **Step 3: 实现最小改动**

在 `backend/runner/config.py` 的 `RunnerConfig` 中新增字段并在 `from_env()` 中解析：

```python
@dataclass
class RunnerConfig:
    # ... 现有字段 ...
    crates_io_user_agent: str = "crateprobe-runner"
    crates_io_rate_limit_rps: float = 1.0
    crates_io_cache_ttl_seconds: float = 300.0
    crates_io_max_concurrent_downloads: int = 3  # 在 from_env 中改为 max_jobs
```

在 `from_env()` 中读取：

```python
crates_io_user_agent = os.environ.get("CRATES_IO_USER_AGENT", "crateprobe-runner")
crates_io_rate_limit_rps_raw = os.environ.get("CRATES_IO_RATE_LIMIT_RPS", "1.0")
crates_io_cache_ttl_seconds_raw = os.environ.get("CRATES_IO_CACHE_TTL_SECONDS", "300.0")
crates_io_max_concurrent_downloads_raw = os.environ.get(
    "CRATES_IO_MAX_CONCURRENT_DOWNLOADS", str(max_jobs_raw)
)
```

并在 `return cls(...)` 中加入：

```python
crates_io_user_agent=crates_io_user_agent,
crates_io_rate_limit_rps=_float("CRATES_IO_RATE_LIMIT_RPS", crates_io_rate_limit_rps_raw),
crates_io_cache_ttl_seconds=_float("CRATES_IO_CACHE_TTL_SECONDS", crates_io_cache_ttl_seconds_raw),
crates_io_max_concurrent_downloads=_int(
    "CRATES_IO_MAX_CONCURRENT_DOWNLOADS", crates_io_max_concurrent_downloads_raw
),
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_config.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/config.py backend/tests/unit/runner/test_config.py
git commit -m "feat(runner): add crates.io policy config env vars"
```

---

## Task 2: 实现 AsyncTokenBucket

**Files:**
- Create: `backend/runner/rate_limiter.py`
- Test: `backend/tests/unit/runner/test_rate_limiter.py`（新增）

**Interfaces:**
- Consumes: 无
- Produces: `AsyncTokenBucket.acquire() -> float`（返回因限速等待的秒数）

- [ ] **Step 1: 写失败测试**

```python
import asyncio
import pytest
from runner.rate_limiter import AsyncTokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_immediate_first_request():
    bucket = AsyncTokenBucket(rate=10.0, capacity=1.0)
    waited = await bucket.acquire()
    assert waited == 0.0


@pytest.mark.asyncio
async def test_token_bucket_limits_rate():
    bucket = AsyncTokenBucket(rate=100.0, capacity=1.0)
    await bucket.acquire()
    waited = await bucket.acquire()
    assert waited > 0.0


@pytest.mark.asyncio
async def test_token_bucket_rate_of_one_per_second():
    bucket = AsyncTokenBucket(rate=1.0, capacity=1.0)
    await bucket.acquire()
    start = asyncio.get_event_loop().time()
    await bucket.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.9
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/unit/runner/test_rate_limiter.py -v
```

Expected: 失败，模块不存在。

- [ ] **Step 3: 实现最小代码**

创建 `backend/runner/rate_limiter.py`：

```python
import asyncio
import time
from typing import Optional


class AsyncTokenBucket:
    def __init__(self, rate: float = 1.0, capacity: Optional[float] = None):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = float(capacity if capacity is not None else rate)
        self._tokens = self.capacity
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_update = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_update = now
            else:
                wait = 0.0

            self._tokens -= 1.0
            return wait
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_rate_limiter.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/rate_limiter.py backend/tests/unit/runner/test_rate_limiter.py
git commit -m "feat(runner): add AsyncTokenBucket rate limiter"
```

---

## Task 3: 实现 TTLCache

**Files:**
- Create: `backend/runner/cache.py`
- Test: `backend/tests/unit/runner/test_cache.py`（新增）

**Interfaces:**
- Consumes: 无
- Produces: `TTLCache.get(*parts) -> Optional[T]`、`TTLCache.set(value, *parts)`

- [ ] **Step 1: 写失败测试**

```python
import pytest
from runner.cache import TTLCache


def test_cache_returns_none_for_missing_key():
    cache = TTLCache(ttl_seconds=60.0)
    assert cache.get("serde") is None


def test_cache_returns_cached_value():
    cache = TTLCache(ttl_seconds=60.0)
    cache.set("1.0.0", "serde")
    assert cache.get("serde") == "1.0.0"


def test_cache_expires_after_ttl():
    now = [0.0]
    cache = TTLCache(ttl_seconds=5.0, time_source=lambda: now[0])
    cache.set("1.0.0", "serde")
    assert cache.get("serde") == "1.0.0"
    now[0] = 5.0
    assert cache.get("serde") is None
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/unit/runner/test_cache.py -v
```

Expected: 失败。

- [ ] **Step 3: 实现最小代码**

创建 `backend/runner/cache.py`：

```python
import time
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(
        self,
        ttl_seconds: float,
        time_source: Callable[[], float] = time.monotonic,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._time_source = time_source
        self._store: dict[str, tuple[T, float]] = {}

    @staticmethod
    def _key(*parts) -> str:
        return "|".join(str(p) for p in parts)

    def get(self, *parts) -> Optional[T]:
        key = self._key(*parts)
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._time_source() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, value: T, *parts) -> None:
        key = self._key(*parts)
        self._store[key] = (value, self._time_source() + self.ttl_seconds)
```

注意：当前实现是同步的（单 key 操作，无 IO），因此不需要 `asyncio.Lock`。如果后续改为持久化缓存，再改为 async。

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_cache.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/cache.py backend/tests/unit/runner/test_cache.py
git commit -m "feat(runner): add in-memory TTL cache"
```

---

## Task 4: 重构 CratesAPI（限速、缓存、User-Agent、退避）

**Files:**
- Modify: `backend/runner/crates_api.py`
- Test: `backend/tests/unit/runner/test_crates_api.py`（新增）

**Interfaces:**
- Consumes: `AsyncTokenBucket`、`TTLCache`
- Produces: `CratesAPI(user_agent, rate_limiter, cache)` 提供 `get_latest_version()`、`verify_version_exists()`；移除 `download_crate()`（移至 Task 5）。

- [ ] **Step 1: 写失败测试**

创建 `backend/tests/unit/runner/test_crates_api.py`：

```python
import httpx
import pytest

from runner.crates_api import CrateNotFoundError, CratesAPI
from runner.cache import TTLCache
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
        cache=None,
    )
    version = await api.get_latest_version("serde")
    assert version == "1.0.0"
    assert sleeps == [1.0]
    await api.close()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/unit/runner/test_crates_api.py -v
```

Expected: 失败，新接口不存在。

- [ ] **Step 3: 实现 CratesAPI 重构**

重写 `backend/runner/crates_api.py`：

```python
import asyncio
import logging
import os
from typing import Optional

import httpx

from runner.cache import TTLCache
from runner.rate_limiter import AsyncTokenBucket

logger = logging.getLogger(__name__)


class CrateNotFoundError(Exception):
    pass


class VersionNotFoundError(Exception):
    pass


class CratesAPI:
    BASE_URL = "https://crates.io/api/v1"
    DEFAULT_USER_AGENT = "crateprobe-runner"
    MAX_RETRIES = 3

    def __init__(
        self,
        user_agent: Optional[str] = None,
        client: Optional[httpx.AsyncClient] = None,
        rate_limiter: Optional[AsyncTokenBucket] = None,
        cache: Optional[TTLCache] = None,
    ):
        self.user_agent = user_agent or os.environ.get(
            "CRATES_IO_USER_AGENT"
        ) or self.DEFAULT_USER_AGENT
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent}
        )
        self.rate_limiter = rate_limiter or AsyncTokenBucket(rate=1.0)
        self.cache = cache or TTLCache(ttl_seconds=300.0)

    async def close(self) -> None:
        await self.client.aclose()

    async def _request(self, url: str) -> httpx.Response:
        cache_key = ("api", url)
        cached = self.cache.get(*cache_key)
        if cached is not None:
            logger.debug(
                "crates.io api cache hit",
                extra={"url": url, "user_agent": self.user_agent},
            )
            return cached

        wait_seconds = await self.rate_limiter.acquire()
        logger.debug(
            "crates.io api request",
            extra={
                "url": url,
                "user_agent": self.user_agent,
                "wait_seconds": wait_seconds,
            },
        )

        last_error: Optional[Exception] = None
        backoff = 1.0
        max_backoff = 60.0

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(url)
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    sleep_time = retry_after if retry_after is not None else min(backoff, max_backoff)
                    logger.warning(
                        "crates.io rate limited",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "retry_after": retry_after,
                            "backoff_seconds": sleep_time,
                        },
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(sleep_time)
                        backoff *= 2
                    continue

                if response.status_code == 404:
                    return response

                response.raise_for_status()
                self.cache.set(response, *cache_key)
                return response
            except httpx.HTTPStatusError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(min(backoff, max_backoff))
                    backoff *= 2
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(min(backoff, max_backoff))
                    backoff *= 2

        raise last_error or RuntimeError(f"request to {url} failed")

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    async def get_latest_version(self, crate_name: str) -> str:
        url = f"{self.BASE_URL}/crates/{crate_name}"
        response = await self._request(url)
        if response.status_code == 404:
            raise CrateNotFoundError(f"Crate '{crate_name}' not found")
        data = response.json()
        return data["crate"]["max_version"]

    async def verify_version_exists(self, crate_name: str, version: str) -> bool:
        url = f"{self.BASE_URL}/crates/{crate_name}"
        response = await self._request(url)
        if response.status_code == 404:
            raise CrateNotFoundError(f"Crate '{crate_name}' not found")
        data = response.json()
        versions = [v["num"] for v in data["versions"]]
        return version in versions
```

注意：这里把 HTTP 响应对象本身缓存，避免重复解析 JSON。调用方需要检查 `status_code` 和解析 JSON。也可以在缓存中存解析后的 dict，但缓存响应对象更通用。

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_crates_api.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/crates_api.py backend/tests/unit/runner/test_crates_api.py
git commit -m "feat(runner): add rate limit, cache, UA and retry to CratesAPI"
```

---

## Task 5: 实现 CrateDownloader

**Files:**
- Create: `backend/runner/crate_downloader.py`
- Test: `backend/tests/unit/runner/test_crate_downloader.py`（新增）

**Interfaces:**
- Consumes: `CratesAPI`
- Produces: `CrateDownloader(crates_api, max_concurrent_downloads=...)` 提供 `resolve_version(crate_name, version=None)` 和 `download(crate_name, version, output_path)`。

- [ ] **Step 1: 写失败测试**

```python
from pathlib import Path

import httpx
import pytest

from runner.crate_downloader import CrateDownloader
from runner.crates_api import CrateNotFoundError, CratesAPI


def _transport_with(responses):
    def handler(request: httpx.Request):
        return responses.pop(0)
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_resolve_version_uses_latest_when_not_specified():
    responses = [
        httpx.Response(200, json={"crate": {"max_version": "1.2.3"}, "versions": []}),
    ]
    crates_api = CratesAPI(client=httpx.AsyncClient(transport=_transport_with(responses)))
    downloader = CrateDownloader(crates_api=crates_api)
    version = await downloader.resolve_version("serde")
    assert version == "1.2.3"
    await crates_api.close()


@pytest.mark.asyncio
async def test_resolve_version_verifies_existing_version():
    responses = [
        httpx.Response(
            200,
            json={
                "crate": {"max_version": "1.2.3"},
                "versions": [{"num": "1.0.0"}, {"num": "1.2.3"}],
            },
        ),
    ]
    crates_api = CratesAPI(client=httpx.AsyncClient(transport=_transport_with(responses)))
    downloader = CrateDownloader(crates_api=crates_api)
    version = await downloader.resolve_version("serde", version="1.0.0")
    assert version == "1.0.0"
    await crates_api.close()


@pytest.mark.asyncio
async def test_download_writes_crate_file(tmp_path):
    def handler(request: httpx.Request):
        assert "static.crates.io" in str(request.url)
        return httpx.Response(200, content=b"crate bytes")

    crates_api = CratesAPI(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda req: httpx.Response(200))))
    downloader = CrateDownloader(crates_api=crates_api)
    output = tmp_path / "serde-1.0.0.crate"
    await downloader.download("serde", "1.0.0", output)
    assert output.read_bytes() == b"crate bytes"
    await crates_api.close()
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
uv run pytest tests/unit/runner/test_crate_downloader.py -v
```

Expected: 失败，模块不存在。

- [ ] **Step 3: 实现 CrateDownloader**

创建 `backend/runner/crate_downloader.py`：

```python
import asyncio
import logging
from pathlib import Path
from typing import Optional

import httpx

from runner.crates_api import CratesAPI, VersionNotFoundError

logger = logging.getLogger(__name__)


class CrateDownloader:
    DOWNLOAD_URL = "https://static.crates.io/crates"
    MAX_RETRIES = 3

    def __init__(
        self,
        crates_api: CratesAPI,
        max_concurrent_downloads: int = 3,
        client: Optional[httpx.AsyncClient] = None,
    ):
        self.crates_api = crates_api
        self.download_semaphore = asyncio.Semaphore(max_concurrent_downloads)
        self.client = client or httpx.AsyncClient()

    async def close(self) -> None:
        await self.crates_api.close()
        await self.client.aclose()

    async def resolve_version(
        self, crate_name: str, version: Optional[str] = None
    ) -> str:
        if version is None:
            return await self.crates_api.get_latest_version(crate_name)

        exists = await self.crates_api.verify_version_exists(crate_name, version)
        if not exists:
            raise VersionNotFoundError(
                f"Version '{version}' of crate '{crate_name}' not found"
            )
        return version

    async def download(
        self, crate_name: str, version: str, output_path: Path
    ) -> None:
        url = f"{self.DOWNLOAD_URL}/{crate_name}/{crate_name}-{version}.crate"
        async with self.download_semaphore:
            await self._download_with_retry(url, output_path, crate_name, version)

    async def _download_with_retry(
        self,
        url: str,
        output_path: Path,
        crate_name: str,
        version: str,
    ) -> None:
        last_error: Optional[Exception] = None
        backoff = 1.0
        max_backoff = 60.0

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(url, timeout=30.0)
                if response.status_code == 404:
                    raise VersionNotFoundError(
                        f"Version '{version}' of crate '{crate_name}' not found"
                    )
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    sleep_time = retry_after if retry_after is not None else min(backoff, max_backoff)
                    logger.warning(
                        "static.crates.io rate limited",
                        extra={
                            "url": url,
                            "attempt": attempt + 1,
                            "retry_after": retry_after,
                            "backoff_seconds": sleep_time,
                        },
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(sleep_time)
                        backoff *= 2
                    continue

                response.raise_for_status()
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(response.content)
                return
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(min(backoff, max_backoff))
                    backoff *= 2

        raise RuntimeError(
            f"Failed to download {crate_name}@{version} from {url} "
            f"after {self.MAX_RETRIES} attempts: {last_error}"
        ) from last_error

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_crate_downloader.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/crate_downloader.py backend/tests/unit/runner/test_crate_downloader.py
git commit -m "feat(runner): add shared CrateDownloader"
```

---

## Task 6: 将 CrateDownloader 接入 TaskExecutor 与 Runner 启动流程

**Files:**
- Modify: `backend/runner/executor.py`
- Modify: `backend/runner/__main__.py`
- Test: `backend/tests/unit/runner/test_executor.py`（更新）

**Interfaces:**
- Consumes: `CrateDownloader`
- Produces: `TaskExecutor(config, client, crate_downloader)`；`__main__` 创建并注入共享实例。

- [ ] **Step 1: 更新 executor.py**

修改 `TaskExecutor`：

```python
from runner.crate_downloader import CrateDownloader

class TaskExecutor:
    def __init__(
        self,
        config: RunnerConfig,
        client: RunnerControlClient,
        crate_downloader: Optional[CrateDownloader] = None,
    ):
        self.config = config
        self.client = client
        self.crate_downloader = crate_downloader or CrateDownloader(
            crates_api=CratesAPI(),
            max_concurrent_downloads=config.crates_io_max_concurrent_downloads,
        )

    async def close(self):
        await self.crate_downloader.close()
```

在 `_prepare_workspace` 中：

```python
await self.crate_downloader.download(crate_name, version, crate_file)
```

保留 `from runner.crates_api import CratesAPI` 的 import，因为默认构造里会用到。

- [ ] **Step 2: 更新 __main__.py**

```python
from runner.cache import TTLCache
from runner.crate_downloader import CrateDownloader
from runner.rate_limiter import AsyncTokenBucket

async def _run() -> None:
    config = RunnerConfig.from_env()
    client = RunnerControlClient(
        base_url=config.server_url,
        runner_id=config.runner_id,
        token=config.runner_token,
        timeout=config.request_timeout_seconds,
    )
    crates_api = CratesAPI(
        user_agent=config.crates_io_user_agent,
        rate_limiter=AsyncTokenBucket(rate=config.crates_io_rate_limit_rps),
        cache=TTLCache(ttl_seconds=config.crates_io_cache_ttl_seconds),
    )
    crate_downloader = CrateDownloader(
        crates_api=crates_api,
        max_concurrent_downloads=config.crates_io_max_concurrent_downloads,
    )
    executor = TaskExecutor(config, client, crate_downloader=crate_downloader)
    worker = RunnerWorker(
        client=client,
        runner_id=config.runner_id,
        executor=executor,
        metrics_interval_seconds=config.metrics_interval_seconds,
        heartbeat_client_factory=client.clone_for_heartbeat,
        max_jobs=config.max_jobs,
    )
    try:
        await worker.run_forever(config.poll_interval_seconds)
    finally:
        await executor.close()
        await client.aclose()
```

- [ ] **Step 3: 更新 test_executor.py 中的初始化**

所有 `executor = TaskExecutor(config=config, client=FakeClient())` 的地方会走默认构造，创建一个真实 `CrateDownloader`。由于测试 mock 了 `_prepare_workspace`，不会真正下载，因此可以接受。但为了清晰，可以在这些测试里显式构造一个假的 downloader：

```python
class FakeDownloader:
    async def download(self, *args, **kwargs):
        pass
    async def close(self):
        pass
    async def resolve_version(self, crate_name, version=None):
        return version or "1.0.0"
```

然后：

```python
executor = TaskExecutor(
    config=config,
    client=FakeClient(),
    crate_downloader=FakeDownloader(),
)
```

对于 `object.__new__(TaskExecutor)` 的测试（如 `test_execute_claimed_task_does_not_block_event_loop_during_docker_prechecks`），把 `executor.crates_api = ...` 改为 `executor.crate_downloader = FakeDownloader()`。

- [ ] **Step 4: 运行测试，确认通过**

```bash
uv run pytest tests/unit/runner/test_executor.py -v
```

Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/runner/executor.py backend/runner/__main__.py backend/tests/unit/runner/test_executor.py
git commit -m "feat(runner): wire CrateDownloader into TaskExecutor"
```

---

## Task 7: 更新 app/main.py 使用合规 User-Agent 并关闭客户端

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py`（更新 mock）

**Interfaces:**
- Consumes: `CratesAPI`
- Produces: `create_task` 中使用带合规 UA 的 `CratesAPI`，并在 finally 中关闭。

- [ ] **Step 1: 修改 app/main.py 的 create_task**

```python
@app.post("/api/tasks", response_model=TaskResponse)
async def create_task(request: CreateTaskRequest):
    crates_api = CratesAPI()
    try:
        version = request.version
        if not version:
            version = await crates_api.get_latest_version(request.crate_name)
        else:
            exists = await crates_api.verify_version_exists(
                request.crate_name, version
            )
            if not exists:
                raise HTTPException(
                    status_code=400, detail=f"Version {version} not found"
                )
        # 保留原有 create_task 剩余逻辑不变
    finally:
        await crates_api.close()
```

- [ ] **Step 2: 更新 test_main.py 的 mock**

当前测试里 mock 了 `app.main.CratesAPI` 为一个返回 FakeCratesAPI 的 lambda。由于我们加了 `try/finally` 并调用 `await crates_api.close()`，需要确保 FakeCratesAPI 有 `async def close()` 方法。

在每个 `class MockCratesAPI` 里添加：

```python
async def close(self):
    pass
```

- [ ] **Step 3: 运行测试，确认通过**

```bash
uv run pytest tests/unit/test_main.py -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add backend/app/main.py backend/tests/unit/test_main.py
git commit -m "feat(app): use compliant CratesAPI User-Agent and close client"
```

---

## Task 8: 全量测试与格式化

**Files:** 所有已修改文件

- [ ] **Step 1: 运行 black 格式化**

```bash
cd backend
uv run black app/ tests/ runner/
```

- [ ] **Step 2: 运行全量单元测试**

```bash
uv run pytest tests/unit/ -v
```

Expected: PASS。

- [ ] **Step 3: 运行集成测试（可选但建议）**

```bash
uv run pytest tests/integration/ -v
```

Expected: PASS。

- [ ] **Step 4: Commit 格式化结果**

```bash
git add -A
git commit -m "style: format crates.io compliance code with black"
```

---

## Spec Coverage Check

| Spec 要求 | 对应 Task |
| --- | --- |
| API 调用 ≤ 1 req/s | Task 2 + Task 4 |
| User-Agent 可配置，默认 `crateprobe-runner` | Task 1 + Task 4 + Task 7 |
| 元数据缓存 | Task 3 + Task 4 |
| 429 / 5xx 指数退避，尊重 Retry-After | Task 4 + Task 5 |
| 共享 `CrateDownloader` | Task 5 + Task 6 |
| static.crates.io 并发下载限制 | Task 5 + Task 6 |
| 日志记录 API 调用/429 | Task 4 + Task 5 |
| 单元测试覆盖 | 每个 Task 都有 |

## Placeholder Scan

已检查，无 TBD/TODO/"实现 later"/"适当处理" 等占位符。每个测试都给出了具体代码。

## Type Consistency Check

- `AsyncTokenBucket.acquire()` 返回 `float`。
- `TTLCache.get/set` 使用 `*parts` 可变 key。
- `CratesAPI` 构造参数在 Task 4、6、7 中一致：`user_agent`, `client`, `rate_limiter`, `cache`。
- `CrateDownloader` 构造参数在 Task 5、6 中一致：`crates_api`, `max_concurrent_downloads`, `client`。
- `TaskExecutor` 新增 `crate_downloader` 参数，Task 6 与测试一致。
