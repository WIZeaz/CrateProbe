import asyncio
import time
from typing import Optional


class AsyncTokenBucket:
    def __init__(self, rate: float = 1.0, capacity: Optional[float] = None):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = rate
        self.capacity = float(capacity if capacity is not None else rate)
        self._tokens = self.capacity
        self._last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_update
            self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
            self._last_update = now

            if self._tokens < 1.0:
                wait = (1.0 - self._tokens) / self.rate
                await asyncio.sleep(wait)
                now = time.monotonic()
                elapsed = now - self._last_update
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                self._last_update = now
            else:
                wait = 0.0

            self._tokens -= 1.0
            return wait
