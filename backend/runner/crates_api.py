"""Crates.io API client for fetching crate metadata."""

import asyncio
import logging
import os
from typing import Optional
from urllib.parse import urlparse

import httpx

from runner.cache import TTLCache
from runner.rate_limiter import AsyncTokenBucket

logger = logging.getLogger(__name__)


class CrateNotFoundError(Exception):
    """Raised when a crate is not found on crates.io."""


class VersionNotFoundError(Exception):
    """Raised when a specific version of a crate is not found."""


class CratesAPI:
    """Client for interacting with the crates.io API metadata endpoints."""

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
        self.user_agent = (
            user_agent
            or os.environ.get("CRATES_IO_USER_AGENT")
            or self.DEFAULT_USER_AGENT
        )
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": self.user_agent}
        )
        self.rate_limiter = rate_limiter or AsyncTokenBucket(rate=1.0)
        self.cache = cache or TTLCache(ttl_seconds=300.0)

    async def close(self) -> None:
        """Close the HTTP session."""
        await self.client.aclose()

    @staticmethod
    def _parse_retry_after(response: httpx.Response) -> Optional[float]:
        value = response.headers.get("Retry-After")
        if value is None:
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @staticmethod
    def _url_parts(url: str):
        parsed = urlparse(url)
        endpoint = parsed.path
        crate_name = endpoint.split("/")[-1] if endpoint else ""
        return crate_name, endpoint

    async def _request(self, url: str) -> httpx.Response:
        crate_name, endpoint = self._url_parts(url)
        cache_key = ("api", url)
        cached = self.cache.get(*cache_key)
        if cached is not None:
            logger.debug(
                "crates.io api cache hit",
                extra={
                    "crate_name": crate_name,
                    "endpoint": endpoint,
                    "user_agent": self.user_agent,
                    "cache_hit": True,
                },
            )
            return cached

        wait_seconds = await self.rate_limiter.acquire()
        logger.debug(
            "crates.io api request",
            extra={
                "crate_name": crate_name,
                "endpoint": endpoint,
                "user_agent": self.user_agent,
                "cache_hit": False,
                "wait_seconds": wait_seconds,
            },
        )

        last_error: Optional[Exception] = None
        backoff = 1.0
        max_backoff = 60.0

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await self.client.get(
                    url, headers={"User-Agent": self.user_agent}
                )
                if response.status_code == 429:
                    retry_after = self._parse_retry_after(response)
                    sleep_time = (
                        retry_after
                        if retry_after is not None
                        else min(backoff, max_backoff)
                    )
                    logger.warning(
                        "crates.io rate limited",
                        extra={
                            "crate_name": crate_name,
                            "endpoint": endpoint,
                            "attempt": attempt + 1,
                            "retry_after": retry_after,
                            "backoff_seconds": sleep_time,
                        },
                    )
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(sleep_time)
                        backoff *= 2
                    else:
                        last_error = httpx.HTTPStatusError(
                            "rate limited",
                            request=response.request,
                            response=response,
                        )
                    continue

                if response.status_code == 404:
                    return response

                response.raise_for_status()
                self.cache.set(response, *cache_key)
                return response
            except httpx.HTTPStatusError as e:
                last_error = e
                if e.response.status_code >= 500:
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(min(backoff, max_backoff))
                        backoff *= 2
                    continue
                raise
            except httpx.HTTPError as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(min(backoff, max_backoff))
                    backoff *= 2

        raise last_error or RuntimeError(f"request to {url} failed")

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
        response = await self._request(url)
        if response.status_code == 404:
            raise CrateNotFoundError(f"Crate '{crate_name}' not found")
        data = response.json()
        return data["crate"]["max_version"]

    async def verify_version_exists(self, crate_name: str, version: str) -> bool:
        """Verify if a specific version of a crate exists.

        Args:
            crate_name: Name of the crate
            version: Version to check

        Returns:
            True if version exists, False otherwise

        Raises:
            CrateNotFoundError: If crate doesn't exist
        """
        url = f"{self.BASE_URL}/crates/{crate_name}"
        response = await self._request(url)
        if response.status_code == 404:
            raise CrateNotFoundError(f"Crate '{crate_name}' not found")
        data = response.json()
        versions = [v["num"] for v in data["versions"]]
        return version in versions
