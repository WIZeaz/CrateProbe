import pytest
from unittest.mock import patch, Mock
from app.utils.resource_limit import ResourceLimiter, LimitMethod


def test_detect_systemd_available():
    """Test detecting systemd-run availability"""
    limiter = ResourceLimiter(use_systemd=True, max_memory_gb=20, max_runtime_hours=24)

    with patch("shutil.which", return_value="/usr/bin/systemd-run"):
        assert limiter.get_limit_method() == LimitMethod.SYSTEMD


def test_detect_systemd_unavailable():
    """Test fallback when systemd-run not available"""
    limiter = ResourceLimiter(use_systemd=True, max_memory_gb=20, max_runtime_hours=24)

    with patch("shutil.which", return_value=None):
        assert limiter.get_limit_method() == LimitMethod.RESOURCE


def test_build_systemd_command():
    """Test building systemd-run command"""
    limiter = ResourceLimiter(use_systemd=True, max_memory_gb=20, max_runtime_hours=24)

    cmd = limiter.build_command(
        ["cargo", "rapx", "-testgen"],
        cwd="/tmp/workspace"
    )

    assert cmd[0] == "systemd-run"
    assert "--user" in cmd
    assert "--scope" in cmd
    assert "--property=MemoryMax=20G" in cmd
    assert "cargo" in cmd


def test_build_resource_command():
    """Test building command with resource limits"""
    limiter = ResourceLimiter(use_systemd=False, max_memory_gb=20, max_runtime_hours=24)

    cmd = limiter.build_command(
        ["cargo", "rapx", "-testgen"],
        cwd="/tmp/workspace"
    )

    # Should return original command (resource limits applied at runtime)
    assert cmd == ["cargo", "rapx", "-testgen"]


def test_apply_resource_limits():
    """Test applying resource limits to current process"""
    limiter = ResourceLimiter(use_systemd=False, max_memory_gb=1, max_runtime_hours=1)

    with patch("resource.setrlimit") as mock_setrlimit:
        limiter.apply_resource_limits()

        # Should have been called for memory and CPU time
        assert mock_setrlimit.call_count >= 2
