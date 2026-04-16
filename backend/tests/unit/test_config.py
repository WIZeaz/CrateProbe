import pytest
from pathlib import Path
from app.config import Config


def test_config_loads_from_file(tmp_path):
    """Test loading configuration from TOML file"""
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[server]
port = 9000
host = "127.0.0.1"

[workspace]
path = "/tmp/test-workspace"

[database]
path = "test.db"

[logging]
level = "DEBUG"
console = true
file = false
file_path = "test.log"

[distributed]
lease_ttl_seconds = 45
runner_offline_seconds = 90

[security]
admin_token = "secret-admin-token"
""")

    config = Config.from_file(str(config_file))

    assert config.server_port == 9000
    assert config.server_host == "127.0.0.1"
    assert config.workspace_path == Path("/tmp/test-workspace")
    assert config.db_path == "test.db"
    assert config.log_level == "DEBUG"
    assert config.log_console is True
    assert config.log_file is False
    assert config.lease_ttl_seconds == 45
    assert config.runner_offline_seconds == 90
    assert config.admin_token == "secret-admin-token"


def test_config_uses_defaults_when_file_missing():
    """Test default configuration when file doesn't exist"""
    config = Config.from_file("nonexistent.toml")

    assert config.server_port == 8000
    assert config.server_host == "0.0.0.0"
    assert config.workspace_path == Path("./workspace")
    assert config.db_path == "tasks.db"
    assert config.log_level == "INFO"
    assert config.log_console is True
    assert config.log_file is True
    assert config.lease_ttl_seconds == 30
    assert config.runner_offline_seconds == 30
    assert config.admin_token == ""


def test_config_creates_workspace_directory(tmp_path):
    """Test that workspace directory is created if missing"""
    workspace = tmp_path / "workspace"
    config = Config(workspace_path=workspace)
    config.ensure_workspace_structure()

    assert workspace.exists()
    assert (workspace / "repos").exists()
    assert (workspace / "logs").exists()


def test_config_loads_without_execution_block():
    import tempfile
    import os
    from app.config import Config

    toml = """
[server]
port = 9000
host = "127.0.0.1"

[workspace]
path = "/tmp/workspace"

[database]
path = "data.db"

[logging]
level = "DEBUG"
console = false
file = false
file_path = "app.log"

[distributed]
lease_ttl_seconds = 60
runner_offline_seconds = 120

[security]
admin_token = "secret"
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".toml", delete=False) as f:
        f.write(toml)
        path = f.name
    try:
        cfg = Config.from_file(path)
        assert cfg.server_port == 9000
        assert cfg.server_host == "127.0.0.1"
        assert str(cfg.workspace_path) == "/tmp/workspace"
        assert cfg.db_path == "data.db"
        assert cfg.log_level == "DEBUG"
        assert cfg.log_console is False
        assert cfg.log_file is False
        assert cfg.lease_ttl_seconds == 60
        assert cfg.runner_offline_seconds == 120
        assert cfg.admin_token == "secret"
    finally:
        os.unlink(path)


def test_runner_config_defaults_metrics_interval_to_10_seconds(monkeypatch):
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://localhost:8080")
    monkeypatch.setenv("RUNNER_ID", "runner-1")
    monkeypatch.setenv("RUNNER_TOKEN", "token-1")

    from runner.config import RunnerConfig

    cfg = RunnerConfig.from_env()
    assert cfg.metrics_interval_seconds == 10.0


def test_runner_config_reads_metrics_interval_from_env(monkeypatch):
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://localhost:8080")
    monkeypatch.setenv("RUNNER_ID", "runner-1")
    monkeypatch.setenv("RUNNER_TOKEN", "token-1")
    monkeypatch.setenv("RUNNER_METRICS_INTERVAL_SECONDS", "5")

    from runner.config import RunnerConfig

    cfg = RunnerConfig.from_env()
    assert cfg.metrics_interval_seconds == 5.0
