import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from docker.errors import ImageNotFound
from runner.docker_runner import DockerRunner
from core.models import ExecutionResult
from core.models import TaskStatus
import asyncio


@pytest.fixture
def docker_runner():
    return DockerRunner(
        image="rust:test",
        max_memory_gb=8,
        max_runtime_seconds=7200,
        max_cpus=2,  # 2 hours
    )


def test_docker_runner_initialization(docker_runner):
    assert docker_runner.image == "rust:test"
    assert docker_runner.max_memory_gb == 8
    assert docker_runner.max_runtime_seconds == 7200
    assert docker_runner.max_cpus == 2
    assert docker_runner.mounts == []


@pytest.mark.asyncio
async def test_run_builds_correct_command(docker_runner, tmp_path):
    """Test that Docker command is built with correct resource limits and wrapped for redirection"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        stdout_log = tmp_path / "stdout.log"
        stderr_log = tmp_path / "stderr.log"

        result = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        # Verify container was created with correct parameters
        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["image"] == "rust:test"
        assert call_kwargs["mem_limit"] == "8g"
        assert call_kwargs["cpu_quota"] == 200000  # 2 CPUs
        # Command should be wrapped with shell redirection
        assert call_kwargs["command"] == [
            "sh",
            "-c",
            'exec cargo rapx > "/workspace/stdout.log" 2> "/workspace/stderr.log"',
        ]
        assert call_kwargs["volumes"] == [f"{workspace.resolve()}:/workspace:rw"]
        # stdout/stderr should be disabled since we're writing to files
        # tty should be enable cause we want colored output in logs
        assert call_kwargs["stdout"] is False
        assert call_kwargs["stderr"] is False
        assert call_kwargs["tty"] is True
        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.COMPLETED
        assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_includes_workspace_and_configured_mounts(tmp_path):
    runner = DockerRunner(
        image="rust:test",
        max_memory_gb=8,
        max_runtime_seconds=7200,
        max_cpus=2,
        mounts=["/host-cache:/cache:ro"],
    )

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        stdout_log = tmp_path / "stdout.log"
        stderr_log = tmp_path / "stderr.log"

        result = await runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["volumes"] == [
            f"{workspace.resolve()}:/workspace:rw",
            "/host-cache:/cache:ro",
        ]
        assert result.state == TaskStatus.COMPLETED


def test_ensure_image_with_if_not_present_policy(docker_runner):
    """Test image check with if-not-present policy"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.images.get.return_value = Mock(tags=["rust:test"])
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True


def test_ensure_image_pulls_when_missing(docker_runner):
    """Test image is pulled when not present"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.images.get.side_effect = ImageNotFound("Image not found")
        mock_client.images.pull.return_value = Mock()
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True
        mock_client.images.pull.assert_called_once_with("rust:test")


@pytest.mark.asyncio
async def test_run_redirects_to_workspace_log_files(docker_runner, tmp_path):
    """Command should be wrapped to redirect stdout/stderr to workspace log files."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        result = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        assert result.exit_code == 0
        assert result.state == TaskStatus.COMPLETED

        # Verify the command redirects to the workspace log files
        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        wrapped_command = call_kwargs["command"]
        assert wrapped_command[0] == "sh"
        assert '> "/workspace/stdout.log"' in wrapped_command[2]
        assert '2> "/workspace/stderr.log"' in wrapped_command[2]


@pytest.mark.asyncio
async def test_run_does_not_use_docker_logs_api(docker_runner, tmp_path):
    """Verify logs() API is not called since we write directly to files."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        # logs() should NOT be called since we redirect to files
        mock_container.logs.assert_not_called()


@pytest.mark.asyncio
async def test_docker_timeout_stops_container(docker_runner, tmp_path):
    """On timeout, the container is stopped and exit_code is -1."""
    import threading

    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()

        # Simulate container that never finishes (blocks until timeout)
        # Docker SDK's container.wait() is a SYNC method, so we use sync blocking
        wait_event = threading.Event()

        def blocking_wait():
            wait_event.wait()  # Block forever
            return {"StatusCode": 0}

        mock_container.wait.side_effect = blocking_wait
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        # Set a very short timeout to trigger the timeout quickly
        docker_runner.max_runtime_seconds = 1

        result = await docker_runner.run(
            command=["blah"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        assert result.state == TaskStatus.TIMEOUT
        assert result.exit_code == -1
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()
        wait_event.set()  # Unblock the wait thread to allow test to finish


@pytest.mark.asyncio
async def test_run_reconciles_workspace_ownership_after_execution(
    docker_runner, tmp_path
):
    """Runner should normalize workspace ownership after container execution."""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        workspace = tmp_path / "workspace"
        workspace.mkdir()
        stdout_log = tmp_path / "stdout.log"
        stderr_log = tmp_path / "stderr.log"

        with patch.object(docker_runner, "_ensure_workspace_ownership") as mock_fix:
            await docker_runner.run(
                command=["cargo", "rapx"],
                workspace_dir=workspace,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )

        mock_fix.assert_called_once_with(workspace)
