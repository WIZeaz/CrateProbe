import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional

import psutil


Executor = Callable[[dict[str, Any]], Awaitable[None]]


async def default_executor(task: dict[str, Any]) -> None:
    command = task.get("command")
    if not command:
        return

    if isinstance(command, str):
        process = await asyncio.create_subprocess_shell(command)
    else:
        process = await asyncio.create_subprocess_exec(*command)

    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(f"Command exited with code {return_code}")


class RunnerWorker:
    def __init__(
        self,
        client: Any,
        runner_id: str,
        executor: Optional[Executor] = None,
        metrics_interval_seconds: float = 10.0,
    ):
        self._client = client
        self._runner_id = runner_id
        self._executor: Executor = executor or default_executor
        self._metrics_interval_seconds = metrics_interval_seconds
        self._is_executing = False
        self._last_metrics_sent_at = 0.0
        self._logger = logging.getLogger(__name__)

    async def _send_metrics_if_due(self, *, force: bool = False) -> None:
        now = asyncio.get_running_loop().time()
        if (
            not force
            and (now - self._last_metrics_sent_at) < self._metrics_interval_seconds
        ):
            return

        payload = {
            "cpu_percent": psutil.cpu_percent(interval=0.0),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage("/").percent,
            "active_tasks": 1 if self._is_executing else 0,
        }
        try:
            await self._client.send_metrics(payload)
            self._last_metrics_sent_at = now
        except Exception as exc:
            self._logger.warning("Failed to send runner metrics: %s", exc)

    async def run_once(self) -> bool:
        await self._send_metrics_if_due(force=True)
        await self._client.heartbeat({"runner_id": self._runner_id})
        claimed = await self._client.claim({"runner_id": self._runner_id})
        if claimed is None:
            return False

        task_id = claimed["id"]
        lease_token = claimed["lease_token"]

        await self._client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
        )

        self._is_executing = True
        try:
            await self._executor(claimed)
        except Exception:
            await self._client.send_event(
                task_id,
                {"lease_token": lease_token, "event_seq": 2, "event_type": "failed"},
            )
            return True
        finally:
            self._is_executing = False

        await self._client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 2, "event_type": "completed"},
        )
        return True

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
