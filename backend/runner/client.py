import asyncio
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

import httpx


class RunnerControlClient:
    """HTTP client for the runner-facing control-plane API (jobs contract)."""

    def __init__(
        self,
        base_url: str,
        runner_id: str,
        token: str,
        timeout: float,
    ):
        self.base_url = base_url
        self.runner_id = runner_id
        self._token = token
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )

    def clone_for_heartbeat(self) -> "RunnerControlClient":
        return RunnerControlClient(
            base_url=self.base_url,
            runner_id=self.runner_id,
            token=self._token,
            timeout=self._timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def heartbeat(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"/api/runners/{self.runner_id}/heartbeat", json=payload
        )
        response.raise_for_status()
        return response.json()

    async def send_metrics(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(
            f"/api/runners/{self.runner_id}/metrics", json=payload
        )
        response.raise_for_status()
        return response.json()

    async def claim(self, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Claim a job. Returns None when no jobs are pending (HTTP 204).

        On success the response dict uses the jobs-API keys: ``job_id``
        (not ``id``), ``crate_name``, ``version``, ``status``, ``runner_id``,
        ``lease_token``, ``lease_expires_at``, ``task_id``, ``task_sha256``.
        """
        response = await self._client.post(
            f"/api/runners/{self.runner_id}/claim", json=payload
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def sync_job(self, job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/runners/{rid}/jobs/{job_id}/sync with retry.

        payload: {lease_token, sync_seq, status, exit_code?, message?}
        """
        return await self._post_with_retry(
            f"/api/runners/{self.runner_id}/jobs/{job_id}/sync", payload
        )

    async def push_file(self, job_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        """POST /api/runners/{rid}/jobs/{job_id}/files with retry.

        payload: {lease_token, path, mode: "append"|"full", content, chunk_seq?}
        """
        return await self._post_with_retry(
            f"/api/runners/{self.runner_id}/jobs/{job_id}/files", payload
        )

    async def download_task_zip(self, task_id: int) -> bytes:
        """GET /api/runners/{rid}/tasks/{task_id}/zip and return raw zip bytes.

        httpx.HTTPStatusError propagates (e.g. 404 when the task definition or
        its zip is missing); the caller turns that into a terminal job failure.
        """
        response = await self._client.get(
            f"/api/runners/{self.runner_id}/tasks/{task_id}/zip"
        )
        response.raise_for_status()
        return response.content

    async def upload_artifact(
        self,
        job_id: int,
        lease_token: str,
        rel_path: str,
        file_path: Union[str, Path],
    ) -> None:
        """POST /api/runners/{rid}/jobs/{job_id}/artifacts (multipart).

        Sends form fields ``lease_token`` and ``path`` plus the file; uses the
        same bearer auth and 5xx/network retry pattern as the JSON endpoints.
        The file is re-opened per attempt so retries re-send full content.
        """
        file_path = Path(file_path)
        url = f"/api/runners/{self.runner_id}/jobs/{job_id}/artifacts"

        async def do_request() -> httpx.Response:
            with file_path.open("rb") as fh:
                return await self._client.post(
                    url,
                    data={"lease_token": lease_token, "path": rel_path},
                    files={"file": (file_path.name, fh)},
                )

        await self._request_with_retry(do_request)

    async def _post_with_retry(
        self, path: str, payload: dict[str, Any], max_attempts: int = 3
    ) -> dict[str, Any]:
        async def do_request() -> httpx.Response:
            return await self._client.post(path, json=payload)

        response = await self._request_with_retry(do_request, max_attempts)
        return response.json()

    async def _request_with_retry(
        self,
        do_request: Callable[[], Awaitable[httpx.Response]],
        max_attempts: int = 3,
    ) -> httpx.Response:
        last_error: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                response = await do_request()
                if 500 <= response.status_code <= 599:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0)
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                return response
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0)
                    continue
                raise

        if last_error is not None:
            raise last_error

        raise RuntimeError("retry loop exhausted unexpectedly")
