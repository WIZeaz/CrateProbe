from pathlib import Path

import httpx
import pytest

from runner.crate_downloader import CrateDownloader
from runner.crates_api import CrateNotFoundError, CratesAPI, VersionNotFoundError


def _transport_with(responses):
    def handler(request: httpx.Request):
        return responses.pop(0)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_resolve_version_uses_latest_when_not_specified():
    responses = [
        httpx.Response(200, json={"crate": {"max_version": "1.2.3"}, "versions": []}),
    ]
    crates_api = CratesAPI(
        client=httpx.AsyncClient(transport=_transport_with(responses))
    )
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
    crates_api = CratesAPI(
        client=httpx.AsyncClient(transport=_transport_with(responses))
    )
    downloader = CrateDownloader(crates_api=crates_api)
    version = await downloader.resolve_version("serde", version="1.0.0")
    assert version == "1.0.0"
    await crates_api.close()


@pytest.mark.asyncio
async def test_resolve_version_raises_when_version_missing():
    responses = [
        httpx.Response(
            200,
            json={
                "crate": {"max_version": "1.2.3"},
                "versions": [{"num": "1.2.3"}],
            },
        ),
    ]
    crates_api = CratesAPI(
        client=httpx.AsyncClient(transport=_transport_with(responses))
    )
    downloader = CrateDownloader(crates_api=crates_api)
    with pytest.raises(VersionNotFoundError):
        await downloader.resolve_version("serde", version="9.9.9")
    await crates_api.close()


@pytest.mark.asyncio
async def test_download_writes_crate_file(tmp_path):
    def handler(request: httpx.Request):
        assert "static.crates.io" in str(request.url)
        return httpx.Response(200, content=b"crate bytes")

    crates_api = CratesAPI(
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: httpx.Response(200))
        )
    )
    downloader = CrateDownloader(
        crates_api=crates_api,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    output = tmp_path / "serde-1.0.0.crate"
    await downloader.download("serde", "1.0.0", output)
    assert output.read_bytes() == b"crate bytes"
    await crates_api.close()
