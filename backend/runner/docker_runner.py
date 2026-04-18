import asyncio
import logging
import os
import shutil
from pathlib import Path
from typing import List, Optional
import time
import docker
from docker.errors import ImageNotFound, APIError
from core.models import TaskStatus
from core.models import ExecutionResult


logger = logging.getLogger(__name__)


def _sync_log_incremental(source: Path, target: Path) -> None:
    """Sync log file incrementally, only copying new content.

    Args:
        source: Source log file path
        target: Target log file path
    """
    try:
        if not source.exists():
            return

        if not target.exists():
            # First time: copy entire file
            shutil.copy2(str(source), str(target))
            return

        source_size = source.stat().st_size
        target_size = target.stat().st_size

        if source_size > target_size:
            # Append only new content
            with open(source, "rb") as src, open(target, "ab") as dst:
                src.seek(target_size)
                dst.write(src.read())
        elif source_size < target_size:
            # Source was truncated (e.g., task restart), copy entire file
            shutil.copy2(str(source), str(target))
    except Exception:
        # Ignore errors during sync (file might be locked temporarily)
        pass


async def _sync_logs_periodically(
    source_stdout: Path,
    source_stderr: Path,
    target_stdout: Path,
    target_stderr: Path,
    interval: float = 2.0,
    stop_event: Optional[asyncio.Event] = None,
):
    """Periodically sync log files from source to target.

    Args:
        source_stdout: Path to stdout.log in workspace
        source_stderr: Path to stderr.log in workspace
        target_stdout: Path to target stdout log (logs dir)
        target_stderr: Path to target stderr log (logs dir)
        interval: Sync interval in seconds
        stop_event: Event to signal stop
    """
    event = stop_event or asyncio.Event()
    while not event.is_set():
        _sync_log_incremental(source_stdout, target_stdout)
        _sync_log_incremental(source_stderr, target_stderr)
        try:
            await asyncio.wait_for(event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


class DockerRunner:
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

    def _ensure_workspace_ownership(self, workspace_dir: Path) -> None:
        """Normalize bind-mounted workspace ownership back to host user."""
        uid_gid = f"{os.getuid()}:{os.getgid()}"
        self.client.containers.run(
            image=self.image,
            command=["chown", "-R", uid_gid, "/workspace"],
            volumes=[f"{workspace_dir.resolve()}:/workspace:rw"],
            working_dir="/workspace",
            user="0:0",
            remove=True,
            stdout=True,
            stderr=True,
        )

    def ensure_workspace_ownership(self, workspace_dir: Path) -> None:
        """Normalize bind-mounted workspace ownership back to host user."""
        self._ensure_workspace_ownership(workspace_dir)

    async def run(
        self,
        command: List[str],
        workspace_dir: Path,
        stdout_log: Path,
        stderr_log: Path,
    ) -> ExecutionResult:
        """
        Run a command in a Docker container with resource limits.

        stdout/stderr are redirected to files in the workspace mount,
        allowing the host to read logs directly from the mounted volume
        for real-time log updates.

        Args:
            command: Command and arguments to execute
            workspace_dir: Host path to mount as /workspace in container
            stdout_log: Path to write stdout (relative to workspace)
            stderr_log: Path to write stderr (relative to workspace)

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

        # Compute log paths inside container (relative to /workspace)
        stdout_container_path = "/workspace/stdout.log"
        stderr_container_path = "/workspace/stderr.log"

        # Wrap command to redirect stdout/stderr to files
        # Use 'exec' to replace shell process, ensuring proper signal handling
        wrapped_command = [
            "sh",
            "-c",
            f'exec {" ".join(command)} > "{stdout_container_path}" 2> "{stderr_container_path}"',
        ]

        # Run container
        container = None
        log_sync_task = None
        stop_sync_event = asyncio.Event()
        command_summary = " ".join(command)
        started_at = time.monotonic()

        logger.info(
            "container command starting",
            extra={
                "command_summary": command_summary,
                "workspace": str(workspace_dir),
            },
        )

        # Source log paths (inside mounted workspace)
        source_stdout = workspace_dir / "stdout.log"
        source_stderr = workspace_dir / "stderr.log"

        # Clean up existing log files to ensure fresh start (e.g., on retry)
        if stdout_log.exists():
            stdout_log.unlink()
        if stderr_log.exists():
            stderr_log.unlink()

        try:
            container = self.client.containers.run(
                image=self.image,
                command=wrapped_command,
                working_dir="/workspace",
                volumes=volumes,
                detach=True,
                stdout=False,  # No need to capture stdout via Docker API
                stderr=False,  # No need to capture stderr via Docker API
                tty=True,  # Enable TTY for colored output in logs
                environment={
                    "CARGO_TERM_COLOR": "always",
                    "TERM": "xterm-256color",
                },
                **resource_limits,
            )

            # Start log sync task for real-time log updates
            log_sync_task = asyncio.create_task(
                _sync_logs_periodically(
                    source_stdout=source_stdout,
                    source_stderr=source_stderr,
                    target_stdout=stdout_log,
                    target_stderr=stderr_log,
                    interval=2.0,
                    stop_event=stop_sync_event,
                )
            )

            timeout_seconds = self.max_runtime_seconds
            loop = asyncio.get_running_loop()

            def _wait_container() -> dict:
                """Wait for container to finish and return result dict."""
                return container.wait()

            wait_future = loop.run_in_executor(None, _wait_container)

            # Use asyncio.wait_for to enforce execution time limit
            try:
                # Wrap wait_future to handle cancellation properly
                wait_task = asyncio.create_task(
                    asyncio.wait_for(wait_future, timeout=timeout_seconds)
                )
                wait_result = await wait_task
                exit_code = wait_result.get("StatusCode", -1)
            except asyncio.CancelledError:
                # Server is shutting down - stop the container
                logger.warning(
                    "container execution cancelled",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                    },
                )
                try:
                    container.stop(timeout=5)
                except Exception:
                    try:
                        container.kill()
                    except Exception:
                        pass
                raise  # Re-raise to propagate cancellation
            except asyncio.TimeoutError:
                # Execution time limit reached - stop the container
                try:
                    container.stop(timeout=10)
                except Exception:
                    container.kill()  # Force kill if it doesn't stop gracefully
                duration_ms = int((time.monotonic() - started_at) * 1000)
                logger.error(
                    "container execution timed out",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                        "timeout_seconds": timeout_seconds,
                        "duration_ms": duration_ms,
                    },
                )
                return ExecutionResult(
                    state=TaskStatus.TIMEOUT,
                    exit_code=-1,
                    message=f"Execution timed out after {timeout_seconds} seconds",
                )

            duration_ms = int((time.monotonic() - started_at) * 1000)
            if exit_code == 0:
                logger.info(
                    "container command completed",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                        "stdout_log": str(stdout_log),
                        "stderr_log": str(stderr_log),
                    },
                )
                return ExecutionResult(
                    state=TaskStatus.COMPLETED,
                    exit_code=exit_code,
                    message="",
                )
            if exit_code == 137:
                logger.warning(
                    "container command exited non-zero",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                        "exit_code": exit_code,
                        "duration_ms": duration_ms,
                        "stdout_log": str(stdout_log),
                        "stderr_log": str(stderr_log),
                    },
                )
                return ExecutionResult(
                    state=TaskStatus.OOM,
                    exit_code=exit_code,
                    message="Process killed by OOM killer (out of memory)",
                )
            logger.warning(
                "container command exited non-zero",
                extra={
                    "command_summary": command_summary,
                    "workspace": str(workspace_dir),
                    "exit_code": exit_code,
                    "duration_ms": duration_ms,
                    "stdout_log": str(stdout_log),
                    "stderr_log": str(stderr_log),
                },
            )
            return ExecutionResult(
                state=TaskStatus.FAILED,
                exit_code=exit_code,
                message=f"Process exited with code {exit_code}",
            )

        except Exception as exc:
            logger.exception(
                "container execution failed",
                extra={
                    "command_summary": command_summary,
                    "workspace": str(workspace_dir),
                },
            )
            # Write error to stderr log file for visibility
            stderr_log.write_text(f"Unexpected error: {exc}")
            return ExecutionResult(
                state=TaskStatus.FAILED,
                exit_code=-1,
                message=f"Unexpected error: {exc}",
            )
        finally:
            # Stop log sync task
            stop_sync_event.set()
            if log_sync_task is not None:
                try:
                    await asyncio.wait_for(log_sync_task, timeout=5.0)
                except asyncio.TimeoutError:
                    log_sync_task.cancel()
                    try:
                        await log_sync_task
                    except asyncio.CancelledError:
                        pass

            # Final sync to ensure all logs are copied
            _sync_log_incremental(source_stdout, stdout_log)
            _sync_log_incremental(source_stderr, stderr_log)

            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
            try:
                self._ensure_workspace_ownership(workspace_dir)
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
