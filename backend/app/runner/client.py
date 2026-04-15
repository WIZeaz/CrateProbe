import asyncio
from typing import Any, Optional

import httpx


class RunnerControlClient:
    def __init__(
        self,
        base_url: str,
        runner_id: str,
        token: str,
        timeout: float,
    ):
        self.runner_id = runner_id
        self._client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
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
        response = await self._client.post(
            f"/api/runners/{self.runner_id}/claim", json=payload
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def send_event(self, task_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._post_with_retry(
            f"/api/runners/{self.runner_id}/tasks/{task_id}/events", payload
        )

    async def send_log_chunk(
        self, task_id: int, log_type: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._post_with_retry(
            f"/api/runners/{self.runner_id}/tasks/{task_id}/logs/{log_type}/chunks",
            payload,
        )

    async def _post_with_retry(
        self, path: str, payload: dict[str, Any], max_attempts: int = 3
    ) -> dict[str, Any]:
        last_error: Optional[Exception] = None

        for attempt in range(max_attempts):
            try:
                response = await self._client.post(path, json=payload)
                if 500 <= response.status_code <= 599:
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(0)
                        continue
                    response.raise_for_status()
                response.raise_for_status()
                return response.json()
            except httpx.RequestError as exc:
                last_error = exc
                if attempt < max_attempts - 1:
                    await asyncio.sleep(0)
                    continue
                raise

        if last_error is not None:
            raise last_error

        raise RuntimeError("retry loop exhausted unexpectedly")
