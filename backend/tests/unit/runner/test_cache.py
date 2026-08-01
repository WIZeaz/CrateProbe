import pytest
from runner.cache import TTLCache


def test_cache_returns_none_for_missing_key():
    cache = TTLCache(ttl_seconds=60.0)
    assert cache.get("serde") is None


def test_cache_returns_cached_value():
    cache = TTLCache(ttl_seconds=60.0)
    cache.set("1.0.0", "serde")
    assert cache.get("serde") == "1.0.0"


def test_cache_expires_after_ttl():
    now = [0.0]
    cache = TTLCache(ttl_seconds=5.0, time_source=lambda: now[0])
    cache.set("1.0.0", "serde")
    assert cache.get("serde") == "1.0.0"
    now[0] = 5.0
    assert cache.get("serde") is None
