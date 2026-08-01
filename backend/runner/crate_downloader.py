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

    async def download(self, crate_name: str, version: str, output_path: Path) -> None:
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
                    sleep_time = (
                        retry_after
                        if retry_after is not None
                        else min(backoff, max_backoff)
                    )
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
