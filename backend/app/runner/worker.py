import asyncio
from typing import Any, Awaitable, Callable, Optional


Executor = Callable[[dict[str, Any]], Awaitable[None]]


async def default_executor(task: dict[str, Any]) -> None:
    command = task.get("command")
    if not command:
        return

    if isinstance(command, str):
        process = await asyncio.create_subprocess_shell(command)
    else:
        process = await asyncio.create_subprocess_exec(*command)

    return_code = await process.wait()
    if return_code != 0:
        raise RuntimeError(f"Command exited with code {return_code}")


class RunnerWorker:
    def __init__(
        self,
        client: Any,
        runner_id: str,
        executor: Optional[Executor] = None,
    ):
        self._client = client
        self._runner_id = runner_id
        self._executor: Executor = executor or default_executor

    async def run_once(self) -> bool:
        await self._client.heartbeat({"runner_id": self._runner_id})
        claimed = await self._client.claim({"runner_id": self._runner_id})
        if claimed is None:
            return False

        task_id = claimed["id"]
        lease_token = claimed["lease_token"]

        await self._client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
        )

        try:
            await self._executor(claimed)
        except Exception:
            await self._client.send_event(
                task_id,
                {"lease_token": lease_token, "event_seq": 2, "event_type": "failed"},
            )
            return True

        await self._client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 2, "event_type": "completed"},
        )
        return True

    async def run_forever(self, poll_interval_seconds: float) -> None:
        while True:
            did_work = await self.run_once()
            if not did_work:
                await asyncio.sleep(poll_interval_seconds)
