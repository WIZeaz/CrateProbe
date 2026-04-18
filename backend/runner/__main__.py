import asyncio
from runner.client import RunnerControlClient
from runner.config import RunnerConfig
from runner.executor import TaskExecutor
from runner.worker import RunnerWorker


async def _run() -> None:
    config = RunnerConfig.from_env()
    client = RunnerControlClient(
        base_url=config.server_url,
        runner_id=config.runner_id,
        token=config.runner_token,
        timeout=config.request_timeout_seconds,
    )
    executor = TaskExecutor(config, client)
    worker = RunnerWorker(
        client=client,
        runner_id=config.runner_id,
        executor=executor,
        metrics_interval_seconds=config.metrics_interval_seconds,
        heartbeat_client_factory=client.clone_for_heartbeat,
    )
    try:
        await worker.run_forever(config.poll_interval_seconds)
    finally:
        await executor.close()
        await client.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
