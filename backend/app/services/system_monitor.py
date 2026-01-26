import psutil
from typing import Dict


class SystemMonitor:
    """Monitor system resources (CPU, memory, disk)"""

    def get_cpu_usage(self) -> float:
        """Get current CPU usage percentage"""
        return psutil.cpu_percent(interval=0.1)

    def get_memory_usage(self) -> Dict[str, float]:
        """Get memory usage statistics"""
        mem = psutil.virtual_memory()
        return {
            "used_mb": mem.used / (1024 * 1024),
            "total_mb": mem.total / (1024 * 1024),
            "percent": mem.percent
        }

    def get_disk_usage(self, path: str = "/") -> Dict[str, float]:
        """Get disk usage statistics"""
        disk = psutil.disk_usage(path)
        return {
            "used_gb": disk.used / (1024 * 1024 * 1024),
            "total_gb": disk.total / (1024 * 1024 * 1024),
            "percent": disk.percent
        }

    def get_system_stats(self) -> Dict:
        """Get all system statistics at once"""
        memory = self.get_memory_usage()
        disk = self.get_disk_usage()

        return {
            "cpu_percent": self.get_cpu_usage(),
            "memory_percent": memory["percent"],
            "memory_used_gb": memory["used_mb"] / 1024,
            "memory_total_gb": memory["total_mb"] / 1024,
            "disk_percent": disk["percent"],
            "disk_used_gb": disk["used_gb"],
            "disk_total_gb": disk["total_gb"]
        }
