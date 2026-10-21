import asyncio
import io
import zipfile
from pathlib import Path

import httpx
import pytest

from core.task_spec import sha256_hex
from runner.task_cache import TaskCache, TaskFetchError


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _http_status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://control.local/api/runners/r/tasks/1/zip")
    response = httpx.Response(status_code=status_code, request=request)
    return httpx.HTTPStatusError(
        f"status {status_code}", request=request, response=response
    )


class FakeClient:
    """Mock of RunnerControlClient.download_task_zip with call counting."""

    def __init__(self, zip_bytes: bytes = b"", error: Exception | None = None):
        self.zip_bytes = zip_bytes
        self.error = error
        self.calls: list[int] = []
        self.delay = 0.0

    async def download_task_zip(self, task_id: int) -> bytes:
        self.calls.append(task_id)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.zip_bytes


GOOD_ZIP = _make_zip({"task.toml": 'name = "t"\n', "scripts/run.sh": "echo hi\n"})
GOOD_SHA = sha256_hex(GOOD_ZIP)


@pytest.mark.asyncio
async def test_ensure_cold_download_extracts_and_writes_marker(tmp_path: Path):
    client = FakeClient(GOOD_ZIP)
    cache = TaskCache(tmp_path / "cache", client)

    result = await cache.ensure(1, GOOD_SHA)

    assert result == tmp_path / "cache" / "1"
    assert (result / "task.toml").read_text() == 'name = "t"\n'
    assert (result / "scripts" / "run.sh").read_text() == "echo hi\n"
    assert (result / ".sha256").read_text().strip() == GOOD_SHA
    assert client.calls == [1]


@pytest.mark.asyncio
async def test_ensure_cache_hit_skips_download(tmp_path: Path):
    client = FakeClient(GOOD_ZIP)
    cache = TaskCache(tmp_path / "cache", client)

    first = await cache.ensure(1, GOOD_SHA)
    second = await cache.ensure(1, GOOD_SHA)

    assert first == second
    assert client.calls == [1]


@pytest.mark.asyncio
async def test_ensure_redownloads_when_sha_changes(tmp_path: Path):
    client = FakeClient(GOOD_ZIP)
    cache = TaskCache(tmp_path / "cache", client)
    await cache.ensure(1, GOOD_SHA)
    assert (tmp_path / "cache" / "1" / "scripts" / "run.sh").exists()

    new_zip = _make_zip({"task.toml": 'name = "v2"\n'})
    new_sha = sha256_hex(new_zip)
    client.zip_bytes = new_zip

    result = await cache.ensure(1, new_sha)

    assert client.calls == [1, 1]
    assert (result / "task.toml").read_text() == 'name = "v2"\n'
    # stale entries from the previous zip are gone after replacement
    assert not (result / "scripts").exists()
    assert (result / ".sha256").read_text().strip() == new_sha


@pytest.mark.asyncio
async def test_ensure_sha_mismatch_raises_and_leaves_no_cache(tmp_path: Path):
    client = FakeClient(GOOD_ZIP)
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="sha256 mismatch"):
        await cache.ensure(1, "0" * 64)

    assert not (tmp_path / "cache" / "1").exists()


@pytest.mark.asyncio
async def test_ensure_404_raises_not_found(tmp_path: Path):
    client = FakeClient(error=_http_status_error(404))
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="not found"):
        await cache.ensure(999, GOOD_SHA)


@pytest.mark.asyncio
async def test_ensure_http_error_other_than_404_raises(tmp_path: Path):
    client = FakeClient(error=_http_status_error(500))
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="500"):
        await cache.ensure(1, GOOD_SHA)


@pytest.mark.asyncio
async def test_ensure_network_error_raises(tmp_path: Path):
    error = httpx.ConnectError(
        "boom", request=httpx.Request("GET", "http://control.local/x")
    )
    client = FakeClient(error=error)
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="download"):
        await cache.ensure(1, GOOD_SHA)


@pytest.mark.asyncio
async def test_ensure_bad_zip_raises(tmp_path: Path):
    bad = b"this is not a zip"
    client = FakeClient(bad)
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="zip"):
        await cache.ensure(1, sha256_hex(bad))

    assert not (tmp_path / "cache" / "1").exists()


@pytest.mark.asyncio
async def test_ensure_zip_slip_dotdot_entry_raises(tmp_path: Path):
    evil = _make_zip({"task.toml": 'name = "t"\n', "../evil.txt": "pwned\n"})
    client = FakeClient(evil)
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="escape"):
        await cache.ensure(1, sha256_hex(evil))

    assert not (tmp_path / "evil.txt").exists()
    assert not (tmp_path / "cache" / "1").exists()


@pytest.mark.asyncio
async def test_ensure_zip_slip_absolute_path_entry_raises(tmp_path: Path):
    evil = _make_zip({"task.toml": 'name = "t"\n', "/abs/evil.txt": "pwned\n"})
    client = FakeClient(evil)
    cache = TaskCache(tmp_path / "cache", client)

    with pytest.raises(TaskFetchError, match="absolute"):
        await cache.ensure(1, sha256_hex(evil))

    assert not (tmp_path / "cache" / "1").exists()


@pytest.mark.asyncio
async def test_ensure_concurrent_calls_download_once(tmp_path: Path):
    client = FakeClient(GOOD_ZIP)
    client.delay = 0.05
    cache = TaskCache(tmp_path / "cache", client)

    results = await asyncio.gather(*(cache.ensure(1, GOOD_SHA) for _ in range(5)))

    assert client.calls == [1]
    assert all(r == results[0] for r in results)
    assert (results[0] / "task.toml").exists()
