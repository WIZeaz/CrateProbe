import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from docker.errors import ImageNotFound
from runner.docker_runner import DockerRunner
from core.models import ExecutionResult
from core.models import TaskStatus
import asyncio
import logging
import threading
import time


@pytest.fixture
def docker_runner():
    return DockerRunner(
        docker_pull_policy="if-not-present",
        mounts=[],
        log_sync_interval_seconds=2.0,
    )


def _run_kwargs(workspace, stdout_log, stderr_log, **overrides):
    kwargs = dict(
        image="rust:test",
        cmd="cargo rapx",
        workspace_dir=workspace,
        container_workspace="/workspace",
        env={},
        cpu=2,
        mem="8g",
        timeout_seconds=7200,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )
    kwargs.update(overrides)
    return kwargs


def test_docker_runner_initialization(docker_runner):
    assert docker_runner.docker_pull_policy == "if-not-present"
    assert docker_runner.mounts == []
    assert docker_runner.log_sync_interval_seconds == 2.0


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
            **_run_kwargs(workspace, stdout_log, stderr_log)
        )

        # Verify container was created with correct parameters
        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["image"] == "rust:test"
        assert call_kwargs["mem_limit"] == "8g"
        assert call_kwargs["memswap_limit"] == "8g"
        assert call_kwargs["cpu_period"] == 100000
        assert call_kwargs["cpu_quota"] == 200000  # 2 CPUs
        # Command should be wrapped with shell redirection
        assert call_kwargs["command"] == [
            "sh",
            "-c",
            'exec cargo rapx > "/workspace/stdout.log" 2> "/workspace/stderr.log"',
        ]
        assert call_kwargs["working_dir"] == "/workspace"
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
async def test_run_without_limits_omits_resource_kwargs(docker_runner, tmp_path):
    """cpu=None / mem=None must omit the corresponding docker limit kwargs."""
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
            **_run_kwargs(workspace, stdout_log, stderr_log, cpu=None, mem=None)
        )

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert "mem_limit" not in call_kwargs
        assert "memswap_limit" not in call_kwargs
        assert "cpu_quota" not in call_kwargs
        assert "cpu_period" not in call_kwargs
        assert result.state == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_with_partial_limits(docker_runner, tmp_path):
    """Only the provided limit should produce docker kwargs."""
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

        # mem only
        await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, cpu=None, mem="512m")
        )
        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["mem_limit"] == "512m"
        assert call_kwargs["memswap_limit"] == "512m"
        assert "cpu_quota" not in call_kwargs
        assert "cpu_period" not in call_kwargs

        # cpu only (index 2: index 1 is the ownership-reconciliation chown run)
        await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, cpu=0.5, mem=None)
        )
        call_kwargs = mock_client.containers.run.call_args_list[2][1]
        assert call_kwargs["cpu_period"] == 100000
        assert call_kwargs["cpu_quota"] == 50000
        assert "mem_limit" not in call_kwargs
        assert "memswap_limit" not in call_kwargs


@pytest.mark.asyncio
async def test_run_uses_container_workspace_for_command_mounts_and_workdir(
    docker_runner, tmp_path
):
    """container_workspace drives redirection paths, bind mount target and working_dir."""
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
            **_run_kwargs(
                workspace, stdout_log, stderr_log, container_workspace="/work"
            )
        )

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["command"] == [
            "sh",
            "-c",
            'exec cargo rapx > "/work/stdout.log" 2> "/work/stderr.log"',
        ]
        assert call_kwargs["volumes"] == [f"{workspace.resolve()}:/work:rw"]
        assert call_kwargs["working_dir"] == "/work"
        assert result.state == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_merges_env_with_term_default(docker_runner, tmp_path):
    """Environment passed to docker = TERM default merged with caller env."""
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

        await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, env={"FOO": "bar"})
        )

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["environment"] == {
            "TERM": "xterm-256color",
            "FOO": "bar",
        }
        assert "CARGO_TERM_COLOR" not in call_kwargs["environment"]


@pytest.mark.asyncio
async def test_run_env_can_override_term(docker_runner, tmp_path):
    """Caller-provided env wins over the TERM default."""
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

        await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, env={"TERM": "dumb"})
        )

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["environment"]["TERM"] == "dumb"


@pytest.mark.asyncio
async def test_run_without_timeout_awaits_container_indefinitely(
    docker_runner, tmp_path
):
    """timeout_seconds=None must not impose any execution time limit."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()

        release_wait = threading.Event()

        def blocking_wait():
            release_wait.wait(timeout=5.0)
            return {"StatusCode": 0}

        mock_container.wait.side_effect = blocking_wait
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        run_task = asyncio.create_task(
            docker_runner.run(
                **_run_kwargs(workspace, stdout_log, stderr_log, timeout_seconds=None)
            )
        )

        # The container "runs" longer than any default timeout would allow;
        # with no timeout configured the task must still be pending.
        await asyncio.sleep(0.5)
        assert not run_task.done()

        release_wait.set()
        result = await asyncio.wait_for(run_task, timeout=2.0)
        assert result.state == TaskStatus.COMPLETED
        assert result.exit_code == 0


@pytest.mark.asyncio
async def test_run_includes_workspace_and_configured_mounts(tmp_path):
    runner = DockerRunner(
        docker_pull_policy="if-not-present",
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

        result = await runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))

        call_kwargs = mock_client.containers.run.call_args_list[0][1]
        assert call_kwargs["volumes"] == [
            f"{workspace.resolve()}:/workspace:rw",
            "/host-cache:/cache:ro",
        ]
        assert result.state == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_ensure_image_with_if_not_present_policy(docker_runner):
    """Test image check with if-not-present policy"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.images.get.return_value = Mock(tags=["rust:test"])
        mock_docker.return_value = mock_client

        result = await docker_runner.ensure_image("rust:test")
        assert result is True


@pytest.mark.asyncio
async def test_ensure_image_pulls_when_missing(docker_runner):
    """Test image is pulled when not present"""
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.images.get.side_effect = ImageNotFound("Image not found")
        mock_client.images.pull.return_value = Mock()
        mock_docker.return_value = mock_client

        result = await docker_runner.ensure_image("rust:test")
        assert result is True
        mock_client.images.pull.assert_called_once_with("rust:test")


@pytest.mark.asyncio
async def test_ensure_image_always_policy_pulls():
    """docker_pull_policy='always' pulls even when the image exists locally."""
    runner = DockerRunner(docker_pull_policy="always")
    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.images.pull.return_value = Mock()
        mock_docker.return_value = mock_client

        result = await runner.ensure_image("rust:test")
        assert result is True
        mock_client.images.pull.assert_called_once_with("rust:test")
        mock_client.images.get.assert_not_called()


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
            **_run_kwargs(workspace, stdout_log, stderr_log)
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

        await docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))

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

        result = await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, timeout_seconds=1)
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

        with patch.object(
            docker_runner, "_ensure_workspace_ownership_sync"
        ) as mock_fix:
            await docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))

        mock_fix.assert_called_once_with(workspace, "rust:test", "/workspace")


@pytest.mark.asyncio
async def test_docker_command_start_logs_info_with_workspace_context(
    docker_runner, tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="runner.docker_runner")
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

        await docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))

    record = next(
        r for r in caplog.records if "container command starting" in r.message.lower()
    )
    assert record.command_summary == "cargo rapx"
    assert record.workspace == str(workspace)


@pytest.mark.asyncio
async def test_docker_cancellation_logs_warning_with_command_summary(
    docker_runner, tmp_path, caplog
):
    caplog.set_level(logging.WARNING, logger="runner.docker_runner")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.side_effect = asyncio.CancelledError()
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        with pytest.raises(asyncio.CancelledError):
            await docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))

    record = next(
        r
        for r in caplog.records
        if "container execution cancelled" in r.message.lower()
    )
    assert record.command_summary == "cargo rapx"


@pytest.mark.asyncio
async def test_docker_timeout_logs_error_with_fields(docker_runner, tmp_path, caplog):
    import threading

    caplog.set_level(logging.ERROR, logger="runner.docker_runner")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()

        wait_event = threading.Event()

        def blocking_wait():
            wait_event.wait()
            return {"StatusCode": 0}

        mock_container.wait.side_effect = blocking_wait
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        result = await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log, timeout_seconds=1)
        )
        wait_event.set()

    assert result.state == TaskStatus.TIMEOUT
    record = next(r for r in caplog.records if "timed out" in r.message.lower())
    assert record.command_summary == "cargo rapx"
    assert record.timeout_seconds == 1
    assert record.duration_ms >= 0


@pytest.mark.asyncio
async def test_docker_nonzero_exit_logs_warning_with_fields(
    docker_runner, tmp_path, caplog
):
    caplog.set_level(logging.WARNING, logger="runner.docker_runner")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {"StatusCode": 2}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        result = await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log)
        )

    assert result.state == TaskStatus.FAILED
    record = next(r for r in caplog.records if "exited non-zero" in r.message.lower())
    assert record.exit_code == 2
    assert record.duration_ms >= 0
    assert record.stdout_log == str(stdout_log)
    assert record.stderr_log == str(stderr_log)


@pytest.mark.asyncio
async def test_docker_completion_logs_info_with_fields(docker_runner, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="runner.docker_runner")
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
            **_run_kwargs(workspace, stdout_log, stderr_log)
        )

    assert result.state == TaskStatus.COMPLETED
    record = next(r for r in caplog.records if "command completed" in r.message.lower())
    assert record.exit_code == 0
    assert record.duration_ms >= 0
    assert record.stdout_log == str(stdout_log)
    assert record.stderr_log == str(stderr_log)


@pytest.mark.asyncio
async def test_docker_unexpected_error_logs_traceback(docker_runner, tmp_path, caplog):
    caplog.set_level(logging.ERROR, logger="runner.docker_runner")
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_client.containers.run.side_effect = RuntimeError("docker crash")
        mock_docker.return_value = mock_client

        result = await docker_runner.run(
            **_run_kwargs(workspace, stdout_log, stderr_log)
        )

    assert result.state == TaskStatus.FAILED
    record = next(
        r for r in caplog.records if "container execution failed" in r.message.lower()
    )
    assert record.exc_info is not None


def test_docker_runner_uses_custom_log_sync_interval():
    runner = DockerRunner(
        docker_pull_policy="if-not-present",
        log_sync_interval_seconds=5.0,
    )

    assert runner.log_sync_interval_seconds == 5.0


@pytest.mark.asyncio
async def test_docker_cancellation_skips_workspace_ownership_reconciliation(
    docker_runner, tmp_path, monkeypatch
):
    """When run() is cancelled, _ensure_workspace_ownership_sync must be skipped
    to avoid blocking shutdown with a slow sync Docker operation."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with patch("docker.from_env") as mock_docker:
        mock_client = Mock()
        mock_container = Mock()

        # Simulate container.wait() blocking until cancelled
        wait_started = threading.Event()

        def blocking_wait():
            wait_started.set()
            # Block indefinitely (simulating long-running container)
            time.sleep(60)
            return {"StatusCode": 0}

        mock_container.wait.side_effect = blocking_wait
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        ownership_called = []

        def track_ownership(*args, **kwargs):
            ownership_called.append(True)

        monkeypatch.setattr(
            docker_runner, "_ensure_workspace_ownership_sync", track_ownership
        )

        run_task = asyncio.create_task(
            docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))
        )

        # Wait for container.wait() to start
        await asyncio.to_thread(wait_started.wait, timeout=2.0)

        # Cancel the task (simulating shutdown)
        run_task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run_task, timeout=2.0)

        # _ensure_workspace_ownership_sync should NOT have been called
        assert len(ownership_called) == 0


@pytest.mark.asyncio
async def test_docker_cancellation_during_container_startup_stops_started_container(
    docker_runner, tmp_path, monkeypatch
):
    """If cancellation happens while starting the container, the started container
    must still be stopped to avoid leaving it running after runner shutdown."""
    stdout_log = tmp_path / "stdout.log"
    stderr_log = tmp_path / "stderr.log"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    mock_container = Mock()
    start_entered = threading.Event()
    allow_start_return = threading.Event()

    def slow_start_container(**kwargs):
        # Simulate container already started in Docker daemon, but API call
        # has not returned container handle to async caller yet.
        start_entered.set()
        allow_start_return.wait(timeout=2.0)
        return mock_container

    monkeypatch.setattr(docker_runner, "_run_container_sync", slow_start_container)

    run_task = asyncio.create_task(
        docker_runner.run(**_run_kwargs(workspace, stdout_log, stderr_log))
    )

    await asyncio.to_thread(start_entered.wait, timeout=1.0)

    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(run_task, timeout=2.0)

    allow_start_return.set()
    await asyncio.sleep(0.05)

    mock_container.stop.assert_called_once()
