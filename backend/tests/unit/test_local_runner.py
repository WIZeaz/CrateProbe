import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.models import TaskStatus
from app.utils.local_runner import LocalRunner
from app.utils.resource_limit import ResourceLimiter
from app.utils.runner_base import ExecutionResult, Runner


@pytest.fixture
def resource_limiter():
    return ResourceLimiter(use_systemd=False, max_memory_gb=4, max_runtime_seconds=3600)


@pytest.fixture
def local_runner(resource_limiter):
    return LocalRunner(limiter=resource_limiter)


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_success(local_runner):
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))
        mock_subprocess.return_value = mock_proc

        result = await local_runner.run(
            command=["echo", "hello"], cwd="/tmp", timeout_seconds=3600
        )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.COMPLETED
        assert result.exit_code == 0
        assert "success" in result.message.lower()


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_failure(local_runner):
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"error"))
        mock_subprocess.return_value = mock_proc

        result = await local_runner.run(
            command=["false"], cwd="/tmp", timeout_seconds=3600
        )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.FAILED
        assert result.exit_code == 1


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_timeout(local_runner):
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        mock_proc.returncode = None
        mock_proc.kill = Mock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_subprocess.return_value = mock_proc

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await local_runner.run(
                command=["sleep", "10"], cwd="/tmp", timeout_seconds=1
            )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.TIMEOUT
        assert result.exit_code == -1
        assert "timed out" in result.message.lower()
        mock_proc.kill.assert_called_once()


def test_local_runner_implements_runner_interface(local_runner):
    assert isinstance(local_runner, Runner)
