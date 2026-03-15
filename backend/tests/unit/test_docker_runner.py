import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from app.utils.docker_runner import DockerRunner, ExecutionResult


@pytest.fixture
def docker_runner():
    return DockerRunner(
        image="rust:test",
        max_memory_gb=8,
        max_runtime_hours=2,
        max_cpus=2
    )


def test_docker_runner_initialization(docker_runner):
    assert docker_runner.image == "rust:test"
    assert docker_runner.max_memory_gb == 8
    assert docker_runner.max_runtime_hours == 2
    assert docker_runner.max_cpus == 2


@pytest.mark.asyncio
async def test_run_builds_correct_command(docker_runner):
    """Test that Docker command is built with correct resource limits"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {'StatusCode': 0}
        mock_container.logs.return_value = b"test output"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        workspace = Path("/tmp/workspace")
        stdout_log = Path("/tmp/stdout.log")
        stderr_log = Path("/tmp/stderr.log")

        result = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log
        )

        # Verify container was created with correct parameters
        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs['image'] == "rust:test"
        assert call_kwargs['mem_limit'] == "8g"
        assert call_kwargs['cpu_quota'] == 200000  # 2 CPUs
        assert call_kwargs['command'] == ["cargo", "rapx"]


from docker.errors import ImageNotFound

def test_ensure_image_with_if_not_present_policy(docker_runner):
    """Test image check with if-not-present policy"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_client.images.get.return_value = Mock(tags=["rust:test"])
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True


def test_ensure_image_pulls_when_missing(docker_runner):
    """Test image is pulled when not present"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_client.images.get.side_effect = ImageNotFound("Image not found")
        mock_client.images.pull.return_value = Mock()
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True
        mock_client.images.pull.assert_called_once_with("rust:test")
