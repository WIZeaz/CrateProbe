import asyncio
import logging
import psutil
from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)


class RunnerWorker:
    def __init__(
        self,
        client: RunnerControlClient,
        runner_id: str,
        executor,
        metrics_interval_seconds: float = 10.0,
    ):
        self._client = client
        self._runner_id = runner_id
        self._executor = executor
        self._metrics_interval_seconds = metrics_interval_seconds
        self._is_executing = False
        self._last_metrics_sent_at = 0.0

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
            "disk_usage_percent": psutil.disk_usage("/").percent,
            "active_tasks": 1 if self._is_executing else 0,
        }
        try:
            await self._client.send_metrics(payload)
            self._last_metrics_sent_at = now
        except Exception as exc:
            logger.warning("Failed to send runner metrics: %s", exc)

    async def run_once(self) -> bool:
        await self._send_metrics_if_due(force=True)
        await self._client.heartbeat({"runner_id": self._runner_id})
        claimed = await self._client.claim({"runner_id": self._runner_id})
        if claimed is None:
            return False

        self._is_executing = True
        try:
            await self._executor.execute_claimed_task(claimed)
        finally:
            self._is_executing = False
        return True

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
