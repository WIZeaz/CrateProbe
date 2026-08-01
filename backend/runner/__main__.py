import asyncio
import logging

from runner.cache import TTLCache
from runner.client import RunnerControlClient
from runner.config import RunnerConfig
from runner.crate_downloader import CrateDownloader
from runner.crates_api import CratesAPI
from runner.executor import TaskExecutor
from runner.rate_limiter import AsyncTokenBucket
from runner.worker import RunnerWorker


async def _run() -> None:
    config = RunnerConfig.from_env()
    client = RunnerControlClient(
        base_url=config.server_url,
        runner_id=config.runner_id,
        token=config.runner_token,
        timeout=config.request_timeout_seconds,
    )
    crates_api = CratesAPI(
        user_agent=config.crates_io_user_agent,
        rate_limiter=AsyncTokenBucket(rate=config.crates_io_rate_limit_rps),
        cache=TTLCache(ttl_seconds=config.crates_io_cache_ttl_seconds),
    )
    crate_downloader = CrateDownloader(
        crates_api=crates_api,
        max_concurrent_downloads=config.crates_io_max_concurrent_downloads,
    )
    executor = TaskExecutor(config, client, crate_downloader=crate_downloader)
    worker = RunnerWorker(
        client=client,
        runner_id=config.runner_id,
        executor=executor,
        metrics_interval_seconds=config.metrics_interval_seconds,
        heartbeat_client_factory=client.clone_for_heartbeat,
        max_jobs=config.max_jobs,
    )
    try:
        await worker.run_forever(config.poll_interval_seconds)
    finally:
        await executor.close()
        await client.aclose()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(_run())


if __name__ == "__main__":
    main()
