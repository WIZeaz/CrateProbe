import asyncio
import logging
import threading

import psutil

try:
    from runner.client import RunnerControlClient
except ModuleNotFoundError:  # pragma: no cover - import context fallback for tests
    RunnerControlClient = object

logger = logging.getLogger(__name__)


class RunnerWorker:
    def __init__(
        self,
        client: RunnerControlClient,
        runner_id: str,
        executor,
        metrics_interval_seconds: float = 10.0,
        heartbeat_interval_seconds: float = 5.0,
        heartbeat_client_factory=None,
        max_jobs: int = 1,
    ):
        self._client = client
        self._runner_id = runner_id
        self._executor = executor
        self._metrics_interval_seconds = metrics_interval_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_client_factory = heartbeat_client_factory
        self._max_jobs = max_jobs
        self._inflight_tasks: set[asyncio.Task] = set()
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
        self._inflight_tasks = {
            task for task in self._inflight_tasks if not task.done()
        }

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

        if not self._has_capacity():
            return False

        try:
            claimed = await self._client.claim(
                {
                    "runner_id": self._runner_id,
                    "jobs": self._current_jobs(),
                    "max_jobs": self._max_jobs,
                }
            )
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
        stop_event = threading.Event()
        heartbeat_thread = self._start_heartbeat_thread(stop_event)
        execution_task = asyncio.create_task(
            self._executor.execute_claimed_task(claimed)
        )
        self._inflight_tasks.add(execution_task)
        try:
            await execution_task
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
            self._inflight_tasks.discard(execution_task)
            self._is_executing = False
            stop_event.set()
            heartbeat_thread.join(timeout=5.0)
        return True

    async def _heartbeat_loop_thread(
        self, interval: float, stop_event: threading.Event, client
    ) -> None:
        while not stop_event.is_set():
            try:
                await client.heartbeat({"runner_id": self._runner_id})
            except Exception as exc:
                logger.warning("Background heartbeat failed: %s", exc)

            if stop_event.wait(interval):
                break

        try:
            await client.aclose()
        except Exception:
            pass

    def _start_heartbeat_thread(self, stop_event: threading.Event) -> threading.Thread:
        client = self._create_heartbeat_client()

        def run() -> None:
            asyncio.run(
                self._heartbeat_loop_thread(
                    self._heartbeat_interval_seconds, stop_event, client
                )
            )

        thread = threading.Thread(target=run, name="runner-heartbeat", daemon=True)
        thread.start()
        return thread

    def _create_heartbeat_client(self):
        if self._heartbeat_client_factory is not None:
            return self._heartbeat_client_factory()

        if hasattr(self._client, "clone_for_heartbeat"):
            return self._client.clone_for_heartbeat()

        return self._client

    def _current_jobs(self) -> int:
        return len(self._inflight_tasks)

    def _has_capacity(self) -> bool:
        return self._current_jobs() < self._max_jobs

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
