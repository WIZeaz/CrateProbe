import shutil
import resource
from enum import Enum
from typing import List


class LimitMethod(str, Enum):
    """Method for applying resource limits"""
    SYSTEMD = "systemd"
    RESOURCE = "resource"


class ResourceLimiter:
    """Utility for applying resource limits to subprocesses"""

    def __init__(self, use_systemd: bool, max_memory_gb: int, max_runtime_hours: int):
        self.prefer_systemd = use_systemd
        self.max_memory_gb = max_memory_gb
        self.max_runtime_hours = max_runtime_hours

    def get_limit_method(self) -> LimitMethod:
        """Determine which method to use for resource limiting"""
        if self.prefer_systemd and shutil.which("systemd-run"):
            return LimitMethod.SYSTEMD
        return LimitMethod.RESOURCE

    def build_command(self, base_cmd: List[str], cwd: str) -> List[str]:
        """Build command with appropriate resource limiting wrapper"""
        method = self.get_limit_method()

        if method == LimitMethod.SYSTEMD:
            return [
                "systemd-run",
                "--user",
                "--scope",
                f"--property=MemoryMax={self.max_memory_gb}G",
                f"--property=CPUQuota=400%",  # Allow using multiple cores
                "--"
            ] + base_cmd
        else:
            # Resource limits will be applied in preexec_fn
            return base_cmd

    def apply_resource_limits(self):
        """Apply resource limits to current process (for use in preexec_fn)"""
        # Memory limit (in bytes)
        memory_bytes = self.max_memory_gb * 1024 * 1024 * 1024
        try:
            resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
        except (ValueError, OSError):
            # Some systems don't support RLIMIT_AS
            pass

        # CPU time limit (in seconds)
        cpu_seconds = self.max_runtime_hours * 3600
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
