import time
from typing import Callable, Generic, Optional, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    def __init__(
        self,
        ttl_seconds: float,
        time_source: Callable[[], float] = time.monotonic,
    ):
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self.ttl_seconds = ttl_seconds
        self._time_source = time_source
        self._store: dict[str, tuple[T, float]] = {}

    @staticmethod
    def _key(*parts) -> str:
        return "|".join(str(p) for p in parts)

    def get(self, *parts) -> Optional[T]:
        key = self._key(*parts)
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._time_source() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, value: T, *parts) -> None:
        key = self._key(*parts)
        self._store[key] = (value, self._time_source() + self.ttl_seconds)
