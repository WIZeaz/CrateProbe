import asyncio
from functools import partial
import logging
import os
import shutil
from pathlib import Path
from typing import Dict, Optional
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
    """Execute tasks in Docker containers with resource limits.

    Runner-level settings (pull policy, extra mounts, log sync interval) are
    fixed at construction; everything task-specific (image, command, limits,
    timeout, env, workspace) is supplied per run from the task spec.
    """

    def __init__(
        self,
        docker_pull_policy: str = "if-not-present",
        log_sync_interval_seconds: float = 2.0,
    ):
        self.docker_pull_policy = docker_pull_policy
        self.log_sync_interval_seconds = log_sync_interval_seconds
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy initialization of Docker client"""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    async def close(self) -> None:
        """Close the underlying Docker client and release connections."""
        if self._client is not None:
            await asyncio.to_thread(self._client.close)
            self._client = None

    async def is_available(self) -> bool:
        """Check if Docker is available on this system"""
        return await asyncio.to_thread(self._is_available_sync)

    def _is_available_sync(self) -> bool:
        if not shutil.which("docker"):
            return False
        try:
            self.client.ping()
            return True
        except Exception:
            return False

    async def ensure_image(self, image: str) -> bool:
        """
        Ensure the Docker image is available locally.

        Args:
            image: Docker image reference to check/pull

        Returns:
            True if image is available, False otherwise
        """
        return await asyncio.to_thread(self._ensure_image_sync, image)

    def _ensure_image_sync(self, image: str) -> bool:
        pull_policy = self.docker_pull_policy
        if pull_policy == "always":
            self.client.images.pull(image)
            return True

        if pull_policy == "never":
            try:
                self.client.images.get(image)
                return True
            except ImageNotFound:
                return False

        # if-not-present: pull only if not available
        try:
            self.client.images.get(image)
            return True
        except ImageNotFound:
            try:
                self.client.images.pull(image)
                return True
            except APIError as e:
                raise RuntimeError(f"Failed to pull image {image}: {e}")

    def _build_resource_limits(self, cpu: Optional[float], mem: Optional[str]) -> dict:
        """Build Docker resource limit parameters from the task spec.

        Missing limits (None) are simply not passed to Docker.
        """
        limits: dict = {}
        if mem is not None:
            limits["mem_limit"] = mem
            limits["memswap_limit"] = mem  # Disable swap
        if cpu is not None:
            # CPU quota: number of CPUs * 100000 (microseconds per period)
            limits["cpu_period"] = 100000
            limits["cpu_quota"] = int(cpu * 100000)
        return limits

    async def ensure_workspace_ownership(
        self, workspace_dir: Path, image: str, container_workspace: str
    ) -> None:
        """Normalize bind-mounted workspace ownership back to host user."""
        await asyncio.to_thread(
            self._ensure_workspace_ownership_sync,
            workspace_dir,
            image,
            container_workspace,
        )

    def _ensure_workspace_ownership_sync(
        self, workspace_dir: Path, image: str, container_workspace: str
    ) -> None:
        uid_gid = f"{os.getuid()}:{os.getgid()}"
        self.client.containers.run(
            image=image,
            command=["chown", "-R", uid_gid, container_workspace],
            volumes=[f"{workspace_dir.resolve()}:{container_workspace}:rw"],
            working_dir=container_workspace,
            user="0:0",
            remove=True,
            stdout=True,
            stderr=True,
        )

    async def _cleanup_container_after_cancel(
        self, container, command_summary: str, workspace_dir: Path
    ) -> None:
        """Best-effort cleanup for containers that surface after cancellation."""
        logger.warning(
            "container started after cancellation; stopping late-started container",
            extra={
                "command_summary": command_summary,
                "workspace": str(workspace_dir),
            },
        )
        await asyncio.to_thread(self._stop_container_sync, container)
        await asyncio.to_thread(self._remove_container_sync, container)

    async def run(
        self,
        *,
        image: str,
        cmd: str,
        workspace_dir: Path,
        container_workspace: str,
        env: Dict[str, str],
        cpu: Optional[float] = None,
        mem: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        stdout_log: Path,
        stderr_log: Path,
    ) -> ExecutionResult:
        """
        Run a command in a Docker container with resource limits.

        stdout/stderr are redirected to files in the workspace mount,
        allowing the host to read logs directly from the mounted volume
        for real-time log updates.

        Args:
            image: Docker image to run
            cmd: Shell command string to execute (wrapped with sh -c)
            workspace_dir: Host path mounted at container_workspace
            container_workspace: Absolute path inside the container where
                workspace_dir is mounted and used as working directory
            env: Extra environment variables for the container
            cpu: CPU core limit (None = no limit)
            mem: Docker-style memory limit string, e.g. "4g" (None = no limit)
            timeout_seconds: Execution time limit (None = no timeout)
            stdout_log: Host path to write stdout
            stderr_log: Host path to write stderr

        Returns:
            Structured execution result with status, exit code, and message
        """
        # Ensure workspace directory exists
        workspace_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)

        # Build resource limits
        resource_limits = self._build_resource_limits(cpu=cpu, mem=mem)

        # Mount the job workspace; no additional runner-level mounts exist.
        volumes = [f"{workspace_dir.resolve()}:{container_workspace}:rw"]

        # Compute log paths inside container (relative to container workspace)
        stdout_container_path = f"{container_workspace}/stdout.log"
        stderr_container_path = f"{container_workspace}/stderr.log"

        # Wrap command to redirect stdout/stderr to files
        # Use 'exec' to replace shell process, ensuring proper signal handling
        wrapped_command = [
            "sh",
            "-c",
            f'exec {cmd} > "{stdout_container_path}" 2> "{stderr_container_path}"',
        ]

        # Run container
        container = None
        log_sync_task = None
        stop_sync_event = asyncio.Event()
        command_summary = cmd
        started_at = time.monotonic()
        cancelled = False

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
            loop = asyncio.get_running_loop()
            start_future = loop.run_in_executor(
                None,
                partial(
                    self._run_container_sync,
                    image=image,
                    command=wrapped_command,
                    working_dir=container_workspace,
                    volumes=volumes,
                    detach=True,
                    stdout=False,  # No need to capture stdout via Docker API
                    stderr=False,  # No need to capture stderr via Docker API
                    tty=True,  # Enable TTY for colored output in logs
                    environment={"TERM": "xterm-256color", **env},
                    **resource_limits,
                ),
            )

            try:
                container = await asyncio.shield(start_future)
            except asyncio.CancelledError:
                cancelled = True

                def _cleanup_when_started(fut) -> None:
                    try:
                        late_container = fut.result()
                    except Exception:
                        return
                    asyncio.create_task(
                        self._cleanup_container_after_cancel(
                            late_container, command_summary, workspace_dir
                        )
                    )

                start_future.add_done_callback(_cleanup_when_started)
                raise

            # Start log sync task for real-time log updates
            log_sync_task = asyncio.create_task(
                _sync_logs_periodically(
                    source_stdout=source_stdout,
                    source_stderr=source_stderr,
                    target_stdout=stdout_log,
                    target_stderr=stderr_log,
                    interval=self.log_sync_interval_seconds,
                    stop_event=stop_sync_event,
                )
            )

            def _wait_container() -> dict:
                """Wait for container to finish and return result dict."""
                return container.wait()

            wait_future = loop.run_in_executor(None, _wait_container)

            # Use asyncio.wait_for to enforce execution time limit
            try:
                if timeout_seconds is None:
                    # No timeout configured: wait for the container directly
                    wait_result = await wait_future
                else:
                    # Wrap wait_future to handle cancellation properly
                    wait_task = asyncio.create_task(
                        asyncio.wait_for(wait_future, timeout=timeout_seconds)
                    )
                    wait_result = await wait_task
                exit_code = wait_result.get("StatusCode", -1)
            except asyncio.CancelledError:
                # Server is shutting down - stop the container
                cancelled = True
                logger.warning(
                    "container execution cancelled",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                    },
                )
                await asyncio.to_thread(self._stop_container_sync, container)
                raise  # Re-raise to propagate cancellation
            except asyncio.TimeoutError:
                # Execution time limit reached - stop the container
                actual_elapsed = time.monotonic() - started_at
                logger.warning(
                    "enforcing execution timeout",
                    extra={
                        "command_summary": command_summary,
                        "workspace": str(workspace_dir),
                        "timeout_seconds": timeout_seconds,
                        "actual_elapsed_seconds": round(actual_elapsed, 2),
                    },
                )
                await asyncio.to_thread(
                    self._stop_container_sync, container, timeout=30
                )
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

        except asyncio.CancelledError:
            cancelled = True
            raise
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
                await asyncio.to_thread(self._remove_container_sync, container)
            # Skip expensive chown on cancellation to avoid blocking shutdown.
            if not cancelled:
                try:
                    await asyncio.to_thread(
                        self._ensure_workspace_ownership_sync,
                        workspace_dir,
                        image,
                        container_workspace,
                    )
                except Exception:
                    pass

    def _run_container_sync(self, **kwargs):
        return self.client.containers.run(**kwargs)

    def _stop_container_sync(self, container, timeout: int = 10) -> None:
        try:
            container.stop(timeout=timeout)
        except Exception:
            try:
                container.kill()
            except Exception:
                pass

    def _remove_container_sync(self, container) -> None:
        try:
            container.remove(force=True)
        except Exception:
            pass
