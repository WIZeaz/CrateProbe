import asyncio
import pytest
from runner.rate_limiter import AsyncTokenBucket


@pytest.mark.asyncio
async def test_token_bucket_allows_immediate_first_request():
    bucket = AsyncTokenBucket(rate=10.0, capacity=1.0)
    waited = await bucket.acquire()
    assert waited == 0.0


@pytest.mark.asyncio
async def test_token_bucket_limits_rate():
    bucket = AsyncTokenBucket(rate=100.0, capacity=1.0)
    await bucket.acquire()
    waited = await bucket.acquire()
    assert waited > 0.0


@pytest.mark.asyncio
async def test_token_bucket_rate_of_one_per_second():
    bucket = AsyncTokenBucket(rate=1.0, capacity=1.0)
    await bucket.acquire()
    start = asyncio.get_event_loop().time()
    await bucket.acquire()
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed >= 0.9
