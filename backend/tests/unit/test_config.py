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

[execution]
max_jobs = 2
max_memory_gb = 10
max_runtime_hours = 12
use_systemd = false

[database]
path = "test.db"

[logging]
level = "DEBUG"
console = true
file = false
file_path = "test.log"
""")

    config = Config.from_file(str(config_file))

    assert config.server_port == 9000
    assert config.server_host == "127.0.0.1"
    assert config.workspace_path == Path("/tmp/test-workspace")
    assert config.max_jobs == 2
    assert config.max_memory_gb == 10
    assert config.max_runtime_hours == 12
    assert config.use_systemd is False


def test_config_uses_defaults_when_file_missing():
    """Test default configuration when file doesn't exist"""
    config = Config.from_file("nonexistent.toml")

    assert config.server_port == 8000
    assert config.server_host == "0.0.0.0"
    assert config.workspace_path == Path("./workspace")
    assert config.max_jobs == 3
    assert config.max_memory_gb == 20
    assert config.max_runtime_hours == 24
    assert config.use_systemd is True


def test_config_creates_workspace_directory(tmp_path):
    """Test that workspace directory is created if missing"""
    workspace = tmp_path / "workspace"
    config = Config(workspace_path=workspace)
    config.ensure_workspace_structure()

    assert workspace.exists()
    assert (workspace / "repos").exists()
    assert (workspace / "logs").exists()
