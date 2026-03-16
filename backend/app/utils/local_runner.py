import asyncio
import os
import signal

from app.models import TaskStatus
from app.utils.resource_limit import ResourceLimiter
from app.utils.runner_base import ExecutionResult, Runner


class LocalRunner(Runner):
    """Execute tasks locally with configured resource limits."""

    def __init__(self, limiter: ResourceLimiter):
        self.limiter = limiter

    def _determine_result(
        self, returncode: int | None, timed_out: bool, timeout_seconds: int
    ) -> ExecutionResult:
        if timed_out:
            return ExecutionResult(
                state=TaskStatus.TIMEOUT,
                exit_code=-1,
                message=f"Execution timed out after {timeout_seconds} seconds",
            )

        if returncode == 0:
            return ExecutionResult(
                state=TaskStatus.COMPLETED,
                exit_code=0,
                message="Completed successfully",
            )

        if returncode in (-signal.SIGKILL, 137):
            return ExecutionResult(
                state=TaskStatus.OOM,
                exit_code=returncode,
                message="Process killed by OOM killer (out of memory)",
            )

        return ExecutionResult(
            state=TaskStatus.FAILED,
            exit_code=returncode if returncode is not None else -1,
            message=f"Process exited with code {returncode}",
        )

    async def run(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        wrapped_command = self.limiter.build_command(command, cwd)

        environment = os.environ.copy()
        environment["CARGO_TERM_COLOR"] = "always"
        if env:
            environment.update(env)

        preexec_fn = None
        if self.limiter.get_limit_method().value == "resource":
            preexec_fn = self.limiter.apply_resource_limits

        proc = await asyncio.create_subprocess_exec(
            *wrapped_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=environment,
            preexec_fn=preexec_fn,
        )

        timed_out = False
        try:
            await asyncio.wait_for(proc.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            timed_out = True

        return self._determine_result(proc.returncode, timed_out, timeout_seconds)
