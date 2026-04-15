from datetime import datetime, timedelta
import asyncio

import pytest

from app.services.runner_metrics_store import RunnerMetricsStore


@pytest.mark.asyncio
async def test_store_returns_latest_and_window_filtered_series():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await store.append("runner-1", now - timedelta(hours=2), 10.0, 20.0, 30.0, 0)
    await store.append("runner-1", now - timedelta(minutes=30), 40.0, 50.0, 60.0, 1)

    latest = await store.get_latest("runner-1")
    assert latest is not None
    assert latest.cpu_percent == 40.0

    one_hour = await store.get_series("runner-1", timedelta(hours=1))
    assert len(one_hour) == 1
    assert one_hour[0].active_tasks == 1


@pytest.mark.asyncio
async def test_store_prunes_points_older_than_max_age():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await store.append("runner-1", now - timedelta(hours=25), 1, 1, 1, 0)
    await store.append("runner-1", now - timedelta(hours=1), 2, 2, 2, 0)

    series = await store.get_series("runner-1", timedelta(hours=24))
    assert len(series) == 1
    assert series[0].cpu_percent == 2


@pytest.mark.asyncio
async def test_store_handles_concurrent_writes():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await asyncio.gather(
        *(store.append("runner-1", now, float(i), 10.0, 20.0, 0) for i in range(50))
    )

    series = await store.get_series("runner-1", timedelta(hours=1))
    assert len(series) == 50


@pytest.mark.asyncio
async def test_store_prunes_old_points_on_query_without_new_append():
    base = datetime(2026, 4, 15, 12, 0, 0)
    now_ref = {"now": base}
    store = RunnerMetricsStore(
        max_age=timedelta(hours=24), now_fn=lambda: now_ref["now"]
    )

    await store.append("runner-1", base - timedelta(hours=23), 10, 10, 10, 0)
    await store.append("runner-1", base - timedelta(hours=1), 20, 20, 20, 0)

    now_ref["now"] = base + timedelta(hours=2)
    series = await store.get_series("runner-1", timedelta(hours=24))

    assert len(series) == 1
    assert series[0].cpu_percent == 20
