"""Crates.io API client for fetching crate metadata and downloads."""

import asyncio
from pathlib import Path
from typing import Optional
import httpx


class CrateNotFoundError(Exception):
    """Raised when a crate is not found on crates.io."""

    pass


class VersionNotFoundError(Exception):
    """Raised when a specific version of a crate is not found."""

    pass


class CratesAPI:
    """Client for interacting with the crates.io API."""

    BASE_URL = "https://crates.io/api/v1"
    DOWNLOAD_URL = "https://static.crates.io/crates"
    MAX_RETRIES = 3
    RETRY_DELAY = 2

    def __init__(self):
        """Initialize the API client."""
        self.client = httpx.AsyncClient()

    async def close(self):
        """Close the HTTP session."""
        await self.client.aclose()

    async def _request_with_retry(self, url: str, **kwargs) -> httpx.Response:
        """Make HTTP request with retry logic.

        Args:
            url: URL to request
            **kwargs: Additional arguments to pass to client.get()

        Returns:
            Response object

        Raises:
            httpx.HTTPError: If all retries fail
        """
        last_error = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                continue

        raise last_error

    async def get_latest_version(self, crate_name: str) -> str:
        """Get the latest version of a crate.

        Args:
            crate_name: Name of the crate

        Returns:
            Latest version string

        Raises:
            CrateNotFoundError: If crate doesn't exist
        """
        url = f"{self.BASE_URL}/crates/{crate_name}"
        headers = {"User-Agent": "experiment-platform"}

        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
                return data["crate"]["max_version"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    raise CrateNotFoundError(f"Crate '{crate_name}' not found")
                raise
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                continue

        raise last_error

    async def verify_version_exists(self, crate_name: str, version: str) -> bool:
        """Verify if a specific version of a crate exists.

        Args:
            crate_name: Name of the crate
            version: Version to check

        Returns:
            True if version exists, False otherwise
        """
        url = f"{self.BASE_URL}/crates/{crate_name}"
        headers = {"User-Agent": "experiment-platform"}

        try:
            response = await self.client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()
            versions = [v["num"] for v in data["versions"]]
            return version in versions
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                raise CrateNotFoundError(f"Crate '{crate_name}' not found")
            raise

    async def download_crate(
        self, crate_name: str, version: str, output_path: Path
    ) -> None:
        """Download a specific version of a crate.

        Args:
            crate_name: Name of the crate
            version: Version to download
            output_path: Where to save the downloaded file

        Raises:
            VersionNotFoundError: If version doesn't exist
        """
        url = f"{self.DOWNLOAD_URL}/{crate_name}/{crate_name}-{version}.crate"
        headers = {"User-Agent": "experiment-platform"}

        response = await self.client.get(url, headers=headers)
        response.raise_for_status()
        content = response.content

        with open(output_path, "wb") as f:
            f.write(content)
