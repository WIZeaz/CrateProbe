import pytest
from app.services.system_monitor import SystemMonitor


def test_get_cpu_usage():
    """Test getting CPU usage percentage"""
    monitor = SystemMonitor()
    cpu_percent = monitor.get_cpu_usage()

    assert isinstance(cpu_percent, float)
    assert 0.0 <= cpu_percent <= 100.0


def test_get_memory_usage():
    """Test getting memory usage stats"""
    monitor = SystemMonitor()
    memory = monitor.get_memory_usage()

    assert "used_mb" in memory
    assert "total_mb" in memory
    assert "percent" in memory
    assert isinstance(memory["used_mb"], float)
    assert isinstance(memory["total_mb"], float)
    assert isinstance(memory["percent"], float)
    assert 0.0 <= memory["percent"] <= 100.0


def test_get_disk_usage():
    """Test getting disk usage stats"""
    monitor = SystemMonitor()
    disk = monitor.get_disk_usage()

    assert "used_gb" in disk
    assert "total_gb" in disk
    assert "percent" in disk
    assert isinstance(disk["used_gb"], float)
    assert isinstance(disk["total_gb"], float)
    assert isinstance(disk["percent"], float)
    assert 0.0 <= disk["percent"] <= 100.0


def test_get_system_stats():
    """Test getting all system stats at once"""
    monitor = SystemMonitor()
    stats = monitor.get_system_stats()

    # Verify flat structure (not nested)
    assert "cpu_percent" in stats
    assert "memory_percent" in stats
    assert "memory_used_gb" in stats
    assert "memory_total_gb" in stats
    assert "disk_percent" in stats
    assert "disk_used_gb" in stats
    assert "disk_total_gb" in stats

    assert isinstance(stats["cpu_percent"], float)
    assert isinstance(stats["memory_percent"], float)
    assert isinstance(stats["disk_percent"], float)
