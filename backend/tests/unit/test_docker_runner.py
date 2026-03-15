import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from docker.errors import ImageNotFound
from app.utils.docker_runner import DockerRunner


@pytest.fixture
def docker_runner():
    return DockerRunner(
        image="rust:test", max_memory_gb=8, max_runtime_hours=2, max_cpus=2
    )


def test_docker_runner_initialization(docker_runner):
    assert docker_runner.image == "rust:test"
    assert docker_runner.max_memory_gb == 8
    assert docker_runner.max_runtime_hours == 2
    assert docker_runner.max_cpus == 2


@pytest.mark.asyncio
async def test_run_builds_correct_command(docker_runner, tmp_path):
    """Test that Docker command is built with correct resource limits"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = iter([b"test output"])
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
        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs["image"] == "rust:test"
        assert call_kwargs["mem_limit"] == "8g"
        assert call_kwargs["cpu_quota"] == 200000  # 2 CPUs
        assert call_kwargs["command"] == ["cargo", "rapx"]


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
async def test_logs_file_content_correct_after_streaming(docker_runner, tmp_path):
    """Log files contain the correct content after streaming completes."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    stdout_chunks = [b"line1\n", b"line2\n"]
    stderr_chunks = [b"err1\n"]

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}

        def logs_side_effect(*args, **kwargs):
            if kwargs.get("stderr") and not kwargs.get("stdout"):
                return iter(stderr_chunks)
            return iter(stdout_chunks)

        mock_container.logs.side_effect = logs_side_effect
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        exit_code = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        assert exit_code == 0
        assert stdout_log.read_text() == "line1\nline2\n"
        assert stderr_log.read_text() == "err1\n"


@pytest.mark.asyncio
async def test_logs_call_uses_stream_and_follow(docker_runner, tmp_path):
    """Verify logs() is called with stream=True and follow=True."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_container.logs.return_value = iter([])
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        calls = mock_container.logs.call_args_list
        assert len(calls) == 2
        for call in calls:
            assert call.kwargs.get("stream") is True
            assert call.kwargs.get("follow") is True


@pytest.mark.asyncio
async def test_docker_timeout_stops_container(docker_runner, tmp_path):
    """On timeout, the container is stopped and exit_code is -1."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.side_effect = Exception("Read timeout")
        mock_container.logs.return_value = iter([])
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        exit_code = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
        )

        assert exit_code == -1
        mock_container.stop.assert_called_once()
        mock_container.remove.assert_called_once()
