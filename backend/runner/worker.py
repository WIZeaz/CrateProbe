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
            "disk_percent": psutil.disk_usage("/").percent,
            "active_tasks": 1 if self._is_executing else 0,
        }
        try:
            await self._client.send_metrics(payload)
            self._last_metrics_sent_at = now
        except Exception as exc:
            logger.warning(
                "failed to send runner metrics: %s",
                exc,
                extra={"runner_id": self._runner_id},
            )

    async def _heartbeat_loop(self, interval: float, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._client.heartbeat({"runner_id": self._runner_id})
                await self._send_metrics_if_due()
            except Exception as exc:
                logger.warning(
                    "background heartbeat failed: %s",
                    exc,
                    extra={"runner_id": self._runner_id},
                )
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    async def run_once(self) -> bool:
        await self._send_metrics_if_due(force=True)
        try:
            await self._client.heartbeat({"runner_id": self._runner_id})
        except Exception as exc:
            logger.warning(
                "runner heartbeat request failed: %s",
                exc,
                extra={"runner_id": self._runner_id},
            )
            raise

        try:
            claimed = await self._client.claim({"runner_id": self._runner_id})
        except Exception as exc:
            logger.warning(
                "runner claim request failed: %s",
                exc,
                extra={"runner_id": self._runner_id},
            )
            raise
        if claimed is None:
            return False

        self._is_executing = True
        stop_event = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(5.0, stop_event))
        try:
            await self._executor.execute_claimed_task(claimed)
        except Exception:
            logger.exception(
                "runner executor failed",
                extra={
                    "runner_id": self._runner_id,
                    "task_id": claimed.get("id"),
                    "crate_name": claimed.get("crate_name"),
                },
            )
            raise
        finally:
            self._is_executing = False
            stop_event.set()
            try:
                await asyncio.wait_for(heartbeat_task, timeout=5.0)
            except asyncio.TimeoutError:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
        return True

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
