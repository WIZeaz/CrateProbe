from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable


@dataclass(frozen=True)
class RunnerMetricPoint:
    ts: datetime
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    active_tasks: int


class RunnerMetricsStore:
    def __init__(
        self,
        max_age: timedelta = timedelta(hours=24),
        now_fn: Callable[[], datetime] | None = None,
    ):
        self._max_age = max_age
        self._now_fn = now_fn or datetime.now
        self._data: dict[str, deque[RunnerMetricPoint]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def append(
        self,
        runner_id: str,
        ts: datetime,
        cpu_percent: float,
        memory_percent: float,
        disk_percent: float,
        active_tasks: int,
    ) -> None:
        async with self._lock:
            bucket = self._data[runner_id]
            bucket.append(
                RunnerMetricPoint(
                    ts=ts,
                    cpu_percent=cpu_percent,
                    memory_percent=memory_percent,
                    disk_percent=disk_percent,
                    active_tasks=active_tasks,
                )
            )
            self._prune_bucket(bucket)

    async def get_latest(self, runner_id: str) -> RunnerMetricPoint | None:
        async with self._lock:
            bucket = self._data.get(runner_id)
            if not bucket:
                return None
            self._prune_bucket(bucket)
            return bucket[-1] if bucket else None

    async def get_series(
        self, runner_id: str, window: timedelta
    ) -> list[RunnerMetricPoint]:
        async with self._lock:
            bucket = self._data.get(runner_id)
            if not bucket:
                return []
            self._prune_bucket(bucket)
            cutoff = self._now_fn() - window
            return [point for point in bucket if point.ts >= cutoff]

    def _prune_bucket(self, bucket: deque[RunnerMetricPoint]) -> None:
        cutoff = self._now_fn() - self._max_age
        while bucket and bucket[0].ts < cutoff:
            bucket.popleft()
