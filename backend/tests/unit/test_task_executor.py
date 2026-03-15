import pytest
from unittest.mock import Mock, patch, AsyncMock, MagicMock
from pathlib import Path
from app.services.task_executor import TaskExecutor
from app.database import Database, TaskRecord
from app.models import TaskStatus
from app.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        workspace_path=tmp_path / "workspace",
        max_memory_gb=1,
        max_runtime_hours=1,
        use_systemd=False,
    )


@pytest.fixture
def db(tmp_path):
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    database.init_db()
    return database


@pytest.fixture
def executor(config, db):
    return TaskExecutor(config, db)


@pytest.mark.asyncio
async def test_prepare_workspace_downloads_crate(executor, config, tmp_path):
    """Test workspace preparation downloads and extracts crate"""
    task_id = 1
    crate_name = "serde"
    version = "1.0.0"

    with patch.object(
        executor.crates_api, "download_crate", new_callable=AsyncMock
    ) as mock_download:
        with patch("tarfile.open") as mock_tarfile:
            mock_tar = MagicMock()
            mock_tarfile.return_value.__enter__.return_value = mock_tar

            workspace_path = await executor.prepare_workspace(
                task_id, crate_name, version
            )

            assert workspace_path.exists()
            mock_download.assert_called_once()
            mock_tar.extractall.assert_called_once()


@pytest.mark.asyncio
async def test_execute_task_updates_database(executor, db, config):
    """Test that task execution updates database status"""
    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "1-stdout.log"),
        str(config.workspace_path / "logs" / "1-stderr.log"),
    )

    with patch.object(
        executor, "prepare_workspace", new_callable=AsyncMock
    ) as mock_prep:
        mock_prep.return_value = config.workspace_path / "repos" / "test-crate-1.0.0"

        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

            task = db.get_task(task_id)
            assert task.status == TaskStatus.COMPLETED
            assert task.finished_at is not None


@pytest.mark.asyncio
async def test_execute_task_handles_failure(executor, db, config):
    """Test that task execution handles process failure"""
    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "1-stdout.log"),
        str(config.workspace_path / "logs" / "1-stderr.log"),
    )

    with patch.object(executor, "prepare_workspace", new_callable=AsyncMock):
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.wait.return_value = 1
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

            task = db.get_task(task_id)
            assert task.status == TaskStatus.FAILED
            assert task.exit_code == 1


@pytest.mark.asyncio
async def test_prepare_workspace_cleans_existing_directory(executor, config):
    """Test that prepare_workspace cleans existing directory for retry"""
    task_id = 1
    crate_name = "serde"
    version = "1.0.0"

    workspace_dir = config.workspace_path / "repos" / f"{crate_name}-{version}"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    # Create some existing files to simulate previous run
    old_file = workspace_dir / "old_file.txt"
    old_file.write_text("old content")

    with patch.object(executor.crates_api, "download_crate", new_callable=AsyncMock):
        with patch("tarfile.open") as mock_tarfile:
            mock_tar = MagicMock()
            mock_tarfile.return_value.__enter__.return_value = mock_tar

            workspace_path = await executor.prepare_workspace(
                task_id, crate_name, version
            )

            # Verify old file was removed
            assert not old_file.exists()
            assert workspace_path.exists()


@pytest.mark.asyncio
async def test_count_generated_items(executor, tmp_path):
    """Test counting testgen output directories"""
    testgen_dir = tmp_path / "testgen"
    tests_dir = testgen_dir / "tests"
    poc_dir = testgen_dir / "poc"

    tests_dir.mkdir(parents=True)
    poc_dir.mkdir(parents=True)

    # Create some test case directories
    (tests_dir / "case1").mkdir()
    (tests_dir / "case2").mkdir()
    (poc_dir / "poc1").mkdir()

    case_count, poc_count = executor.count_generated_items(tmp_path)

    assert case_count == 2
    assert poc_count == 1


@pytest.fixture
def mock_config():
    config = Mock(spec=Config)
    config.execution_mode = "docker"
    config.docker_image = "rust:test"
    config.docker_pull_policy = "if-not-present"
    config.max_memory_gb = 8
    config.max_runtime_hours = 2
    config.max_cpus = 4
    config.workspace_path = Path("/tmp/workspace")
    return config


@pytest.fixture
def mock_database():
    db = Mock()
    return db


@pytest.mark.asyncio
async def test_task_executor_uses_docker_when_configured(mock_config, mock_database):
    """Test that TaskExecutor uses DockerRunner when execution_mode is docker"""
    with patch("app.services.task_executor.DockerRunner") as mock_runner_class:
        mock_runner = Mock()
        mock_runner.is_available.return_value = True
        mock_runner.run = AsyncMock(return_value=0)
        mock_runner_class.return_value = mock_runner

        executor = TaskExecutor(mock_config, mock_database)

        # Verify DockerRunner was initialized
        mock_runner_class.assert_called_once_with(
            image="rust:test", max_memory_gb=8, max_runtime_hours=2, max_cpus=4
        )


import logging


@pytest.mark.asyncio
async def test_execute_task_creates_runner_log(executor, db, config, tmp_path):
    """Runner log file is created when a task executes"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stderr.log"),
    )

    with patch.object(
        executor, "prepare_workspace", new_callable=AsyncMock
    ) as mock_prep:
        mock_prep.return_value = config.workspace_path / "repos" / "test-crate-1.0.0"
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

    runner_log = config.workspace_path / "logs" / f"{task_id}-runner.log"
    assert runner_log.exists(), "Runner log file must be created"
    content = runner_log.read_text()
    assert "started" in content.lower()


@pytest.mark.asyncio
async def test_execute_task_runner_log_uses_task_id(executor, db, config):
    """Runner log path uses task ID, not crate name"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "serde",
        "1.0.0",
        str(config.workspace_path / "repos" / "serde-1.0.0"),
        str(config.workspace_path / "logs" / "serde-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "serde-1.0.0-stderr.log"),
    )

    with patch.object(
        executor, "prepare_workspace", new_callable=AsyncMock
    ) as mock_prep:
        mock_prep.return_value = config.workspace_path / "repos" / "serde-1.0.0"
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 99
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

    expected_path = config.workspace_path / "logs" / f"{task_id}-runner.log"
    bad_path = config.workspace_path / "logs" / "serde-1.0.0-runner.log"
    assert expected_path.exists()
    assert not bad_path.exists()


@pytest.mark.asyncio
async def test_execute_task_runner_log_records_exception(executor, db, config):
    """Runner log captures exceptions that cause task failure"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "bad-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "bad-crate-1.0.0"),
        str(config.workspace_path / "logs" / "bad-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "bad-crate-1.0.0-stderr.log"),
    )

    with patch.object(
        executor,
        "prepare_workspace",
        new_callable=AsyncMock,
        side_effect=RuntimeError("Download failed: connection refused"),
    ):
        await executor.execute_task(task_id)

    runner_log = config.workspace_path / "logs" / f"{task_id}-runner.log"
    assert runner_log.exists()
    content = runner_log.read_text()
    assert "Download failed" in content or "ERROR" in content


@pytest.mark.asyncio
async def test_runner_logger_named_by_task_id(executor, db, config):
    """Runner logger is named f'task.{task_id}' to avoid collisions"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stderr.log"),
    )

    captured_logger_names = []
    original_get_logger = logging.getLogger

    def spy_get_logger(name=None):
        if name and name.startswith("task."):
            captured_logger_names.append(name)
        return original_get_logger(name)

    with patch("logging.getLogger", side_effect=spy_get_logger):
        with patch.object(
            executor, "prepare_workspace", new_callable=AsyncMock
        ) as mock_prep:
            mock_prep.return_value = (
                config.workspace_path / "repos" / "test-crate-1.0.0"
            )
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = AsyncMock()
                mock_process.pid = 1
                mock_process.wait.return_value = 0
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process
                await executor.execute_task(task_id)

    assert f"task.{task_id}" in captured_logger_names
