import asyncio

from app.runner.client import RunnerControlClient
from app.runner.config import RunnerConfig
from app.runner.worker import RunnerWorker


async def _run() -> None:
    config = RunnerConfig.from_env()
    client = RunnerControlClient(
        base_url=config.server_url,
        runner_id=config.runner_id,
        token=config.runner_token,
        timeout=config.request_timeout_seconds,
    )

    worker = RunnerWorker(client=client, runner_id=config.runner_id)
    try:
        await worker.run_forever(config.poll_interval_seconds)
    finally:
        await client.aclose()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
