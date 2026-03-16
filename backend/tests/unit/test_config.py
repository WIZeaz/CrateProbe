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
max_runtime_seconds = 43200  # 12 hours
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
    assert config.max_runtime_seconds == 43200
    assert config.use_systemd is False
    assert config.docker_mounts == []


def test_config_uses_defaults_when_file_missing():
    """Test default configuration when file doesn't exist"""
    config = Config.from_file("nonexistent.toml")

    assert config.server_port == 8000
    assert config.server_host == "0.0.0.0"
    assert config.workspace_path == Path("./workspace")
    assert config.max_jobs == 3
    assert config.max_memory_gb == 20
    assert config.max_runtime_seconds == 86400
    assert config.use_systemd is True
    assert config.docker_mounts == []


def test_config_creates_workspace_directory(tmp_path):
    """Test that workspace directory is created if missing"""
    workspace = tmp_path / "workspace"
    config = Config(workspace_path=workspace)
    config.ensure_workspace_structure()

    assert workspace.exists()
    assert (workspace / "repos").exists()
    assert (workspace / "logs").exists()


def test_config_loads_docker_settings():
    """Test that docker configuration is loaded correctly"""
    import tempfile
    import os

    config_content = b"""
[execution]
execution_mode = "docker"
max_jobs = 5
max_memory_gb = 16
max_runtime_hours = 8
max_cpus = 4

[execution.docker]
image = "my-rust-image:latest"
pull_policy = "always"
"""

    with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".toml") as f:
        f.write(config_content)
        config_path = f.name

    try:
        config = Config.from_file(config_path)
        assert config.execution_mode == "docker"
        assert config.max_cpus == 4
        assert config.docker_image == "my-rust-image:latest"
        assert config.docker_pull_policy == "always"
    finally:
        os.unlink(config_path)


def test_config_loads_docker_mounts(tmp_path):
    """Test that docker mounts are loaded correctly"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data", "/var/log:/logs:ro"]
"""
    )

    config = Config.from_file(str(config_file))

    assert config.docker_mounts == [
        "/host/data:/container/data",
        "/var/log:/logs:ro",
    ]


def test_config_rejects_invalid_docker_mount_format(tmp_path):
    """Test that invalid mount format is rejected"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host-only"]
"""
    )

    with pytest.raises(ValueError, match="Invalid docker mount format"):
        Config.from_file(str(config_file))


def test_config_rejects_non_list_docker_mounts(tmp_path):
    """Test that non-list docker mounts value is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = "/host/data:/container/data"
"""
    )

    with pytest.raises(ValueError, match="Invalid docker mounts: expected a list"):
        Config.from_file(str(config_file))


def test_config_rejects_relative_docker_mount_host_path(tmp_path):
    """Test that relative host path in mount is rejected"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["relative/path:/container/data"]
"""
    )

    with pytest.raises(ValueError, match="Docker mount host path must be absolute"):
        Config.from_file(str(config_file))


def test_config_rejects_relative_docker_mount_container_path(tmp_path):
    """Test that relative container path in mount is rejected"""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:relative/container"]
"""
    )

    with pytest.raises(
        ValueError, match="Docker mount container path must be absolute"
    ):
        Config.from_file(str(config_file))


def test_config_rejects_empty_mode_in_docker_mount(tmp_path):
    """Test that empty mode in 3-part mount is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data:"]
"""
    )

    with pytest.raises(ValueError, match="Invalid docker mount mode"):
        Config.from_file(str(config_file))


def test_config_rejects_invalid_docker_mount_mode(tmp_path):
    """Test that unsupported docker mount mode is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data:banana"]
"""
    )

    with pytest.raises(ValueError, match="Invalid docker mount mode"):
        Config.from_file(str(config_file))


def test_config_accepts_docker_mount_mode_with_selinux_option(tmp_path):
    """Test that Docker-compatible SELinux mount mode is accepted."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data:ro,z"]
"""
    )

    config = Config.from_file(str(config_file))

    assert config.docker_mounts == ["/host/data:/container/data:ro,z"]


def test_config_rejects_conflicting_read_only_and_read_write_mount_modes(tmp_path):
    """Test that conflicting ro,rw mode combination is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data:ro,rw"]
"""
    )

    with pytest.raises(ValueError, match="conflicting options"):
        Config.from_file(str(config_file))


def test_config_rejects_conflicting_selinux_mount_modes(tmp_path):
    """Test that conflicting z,Z SELinux mode combination is rejected."""
    config_file = tmp_path / "config.toml"
    config_file.write_text(
        """
[execution]
execution_mode = "docker"

[execution.docker]
mounts = ["/host/data:/container/data:z,Z"]
"""
    )

    with pytest.raises(ValueError, match="conflicting options"):
        Config.from_file(str(config_file))
