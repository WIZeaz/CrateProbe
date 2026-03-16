import asyncio
import shutil
from pathlib import Path
from typing import List, Optional
import docker
from docker.errors import ImageNotFound, APIError
from app.models import TaskStatus
from app.utils.runner_base import Runner, ExecutionResult


class DockerRunner(Runner):
    """Execute tasks in Docker containers with resource limits"""

    def __init__(
        self,
        image: str,
        max_memory_gb: int,
        max_runtime_seconds: int,
        max_cpus: int,
        mounts: Optional[List[str]] = None,
    ):
        self.image = image
        self.max_memory_gb = max_memory_gb
        self.max_runtime_seconds = max_runtime_seconds
        self.max_cpus = max_cpus
        self.mounts = mounts or []
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy initialization of Docker client"""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def ensure_image(self, pull_policy: str = "if-not-present") -> bool:
        """
        Ensure the Docker image is available locally.

        Args:
            pull_policy: "always", "if-not-present", or "never"

        Returns:
            True if image is available, False otherwise
        """
        if pull_policy == "always":
            self.client.images.pull(self.image)
            return True

        if pull_policy == "never":
            try:
                self.client.images.get(self.image)
                return True
            except ImageNotFound:
                return False

        # if-not-present: pull only if not available
        try:
            self.client.images.get(self.image)
            return True
        except ImageNotFound:
            try:
                self.client.images.pull(self.image)
                return True
            except APIError as e:
                raise RuntimeError(f"Failed to pull image {self.image}: {e}")

    def _build_resource_limits(self) -> dict:
        """Build Docker resource limit parameters"""
        # CPU quota: number of CPUs * 100000 (microseconds per period)
        cpu_quota = int(self.max_cpus * 100000)

        return {
            "mem_limit": f"{self.max_memory_gb}g",
            "memswap_limit": f"{self.max_memory_gb}g",  # Disable swap
            "cpu_quota": cpu_quota,
            "cpu_period": 100000,
        }

    async def run(
        self,
        command: List[str],
        workspace_dir: Path,
        stdout_log: Path,
        stderr_log: Path,
    ) -> ExecutionResult:
        """
        Run a command in a Docker container with resource limits.

        Allocates a pseudo-TTY (tty=True) to ensure applications output ANSI color codes.

        Args:
            command: Command and arguments to execute
            workspace_dir: Host path to mount as /workspace in container
            stdout_log: Path to write stdout
            stderr_log: Path to write stderr

        Returns:
            Structured execution result with status, exit code, and message
        """
        # Ensure workspace directory exists
        workspace_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)

        # Build resource limits
        resource_limits = self._build_resource_limits()

        # Prepare volume mounts
        volumes = [f"{workspace_dir.resolve()}:/workspace:rw"] + self.mounts

        # Run container
        container = None
        try:
            container = self.client.containers.run(
                image=self.image,
                command=command,
                working_dir="/workspace",
                volumes=volumes,
                detach=True,
                stdout=True,
                stderr=True,
                tty=True,
                environment={
                    "CARGO_TERM_COLOR": "always",
                    "TERM": "xterm-256color",
                },
                **resource_limits,
            )

            timeout_seconds = self.max_runtime_seconds
            loop = asyncio.get_running_loop()

            def _stream_to_file(log_path: Path, log_kwargs: dict) -> None:
                """Stream Docker logs to a file, writing each chunk as it arrives.

                log_kwargs is passed as a plain positional arg because
                run_in_executor() does not support keyword argument forwarding.
                """
                with log_path.open("w", encoding="utf-8", errors="replace") as f:
                    for chunk in container.logs(stream=True, follow=True, **log_kwargs):
                        if isinstance(chunk, bytes):
                            f.write(chunk.decode("utf-8", errors="replace"))
                        else:
                            f.write(chunk)
                        f.flush()

            def _wait_container() -> dict:
                """Wait for container to finish and return result dict."""
                return container.wait()

            # Run wait and both log streams concurrently in thread executors.
            # Each runs in a separate thread since Docker SDK is synchronous.
            # IMPORTANT: pass log_kwargs as a positional arg — run_in_executor
            # does not support **kwargs forwarding.
            wait_future = loop.run_in_executor(None, _wait_container)
            stdout_future = loop.run_in_executor(
                None, _stream_to_file, stdout_log, {"stdout": True, "stderr": False}
            )
            stderr_future = loop.run_in_executor(
                None, _stream_to_file, stderr_log, {"stdout": False, "stderr": True}
            )

            # Use asyncio.wait_for to enforce execution time limit
            # This properly cancels the wait and stops the container on timeout
            try:
                wait_result = await asyncio.wait_for(
                    wait_future, timeout=timeout_seconds
                )
                exit_code = wait_result.get("StatusCode", -1)
            except asyncio.TimeoutError:
                # Execution time limit reached - stop the container
                try:
                    container.stop(timeout=10)
                except Exception:
                    container.kill()  # Force kill if it doesn't stop gracefully
                return ExecutionResult(
                    state=TaskStatus.TIMEOUT,
                    exit_code=-1,
                    message=f"Execution timed out after {timeout_seconds} seconds",
                )

            # Wait for log streaming to complete (they should finish shortly after container stops)
            try:
                await asyncio.wait_for(
                    asyncio.gather(stdout_future, stderr_future), timeout=10
                )
            except asyncio.TimeoutError:
                pass  # Log streaming may hang, but we have the exit code

            if exit_code == 0:
                return ExecutionResult(
                    state=TaskStatus.COMPLETED,
                    exit_code=exit_code,
                    message="",
                )
            if exit_code == 137:
                return ExecutionResult(
                    state=TaskStatus.OOM,
                    exit_code=exit_code,
                    message="Process killed by OOM killer (out of memory)",
                )
            return ExecutionResult(
                state=TaskStatus.FAILED,
                exit_code=exit_code,
                message=f"Process exited with code {exit_code}",
            )

        except Exception as e:
            stderr_log.write_text(f"Unexpected error: {e}")
            return ExecutionResult(
                state=TaskStatus.FAILED,
                exit_code=-1,
                message=f"Unexpected error: {e}",
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass

    def is_available(self) -> bool:
        """Check if Docker is available on this system"""
        if not shutil.which("docker"):
            return False

        try:
            self.client.ping()
            return True
        except Exception:
            return False
