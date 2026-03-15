import asyncio
import shutil
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
import docker
from docker.errors import ImageNotFound, APIError


@dataclass
class ExecutionResult:
    """Result of container execution"""
    exit_code: int
    stdout: str
    stderr: str


class DockerRunner:
    """Execute tasks in Docker containers with resource limits"""

    def __init__(
        self,
        image: str,
        max_memory_gb: int,
        max_runtime_hours: int,
        max_cpus: int
    ):
        self.image = image
        self.max_memory_gb = max_memory_gb
        self.max_runtime_hours = max_runtime_hours
        self.max_cpus = max_cpus
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
            "stop_timeout": self.max_runtime_hours * 3600,
        }

    async def run(
        self,
        command: List[str],
        workspace_dir: Path,
        stdout_log: Path,
        stderr_log: Path
    ) -> int:
        """
        Run a command in a Docker container with resource limits.

        Args:
            command: Command and arguments to execute
            workspace_dir: Host path to mount as /workspace in container
            stdout_log: Path to write stdout
            stderr_log: Path to write stderr

        Returns:
            Container exit code
        """
        # Ensure workspace directory exists
        workspace_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)

        # Build resource limits
        resource_limits = self._build_resource_limits()

        # Prepare volume mounts
        volumes = {
            str(workspace_dir.resolve()): {
                "bind": "/workspace",
                "mode": "rw"
            }
        }

        # Run container
        try:
            container = self.client.containers.run(
                image=self.image,
                command=command,
                working_dir="/workspace",
                volumes=volumes,
                detach=True,
                stdout=True,
                stderr=True,
                **resource_limits
            )

            # Wait for container with timeout
            timeout_seconds = self.max_runtime_hours * 3600
            try:
                result = container.wait(timeout=timeout_seconds)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout or error - stop the container
                container.stop(timeout=10)
                exit_code = -1

            # Get logs
            logs = container.logs(stdout=True, stderr=False).decode('utf-8', errors='replace')
            stderr_logs = container.logs(stdout=False, stderr=True).decode('utf-8', errors='replace')

            # Write logs to files
            stdout_log.write_text(logs)
            stderr_log.write_text(stderr_logs)

            # Cleanup
            container.remove(force=True)

            return exit_code

        except APIError as e:
            # Write error to stderr log
            stderr_log.write_text(f"Docker API error: {e}")
            return -1
        except Exception as e:
            stderr_log.write_text(f"Unexpected error: {e}")
            return -1

    def is_available(self) -> bool:
        """Check if Docker is available on this system"""
        if not shutil.which("docker"):
            return False

        try:
            self.client.ping()
            return True
        except Exception:
            return False
