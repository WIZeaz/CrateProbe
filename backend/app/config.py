import sys
from pathlib import Path
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class Config:
    """Application configuration"""

    server_port: int = 8000
    server_host: str = "0.0.0.0"
    workspace_path: Path = Path("./workspace")
    max_jobs: int = 3
    max_memory_gb: int = 20
    max_runtime_seconds: int = 86400  # 24 hours
    max_cpus: int = 4
    use_systemd: bool = True
    execution_mode: str = "systemd"
    docker_image: str = "rust-cargo-rapx:latest"
    docker_pull_policy: str = "if-not-present"
    docker_mounts: list[str] = field(default_factory=list)
    db_path: str = "tasks.db"
    log_level: str = "INFO"
    log_console: bool = True
    log_file: bool = True
    log_file_path: str = "server.log"

    @classmethod
    def from_file(cls, path: str) -> "Config":
        """Load configuration from TOML file, use defaults if file doesn't exist"""
        config_path = Path(path)

        if not config_path.exists():
            return cls()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        execution = data.get("execution", {})
        docker_config = execution.get("docker", {})
        docker_mounts = docker_config.get("mounts", [])

        if docker_mounts is None:
            docker_mounts = []

        validated_mounts = cls._validate_docker_mounts(docker_mounts)

        return cls(
            server_port=data.get("server", {}).get("port", 8000),
            server_host=data.get("server", {}).get("host", "0.0.0.0"),
            workspace_path=Path(data.get("workspace", {}).get("path", "./workspace")),
            max_jobs=execution.get("max_jobs", 3),
            max_memory_gb=execution.get("max_memory_gb", 20),
            max_runtime_seconds=execution.get("max_runtime_seconds", 86400),
            max_cpus=execution.get("max_cpus", 4),
            use_systemd=execution.get("use_systemd", True),
            execution_mode=execution.get("execution_mode", "systemd"),
            docker_image=docker_config.get("image", "rust-cargo-rapx:latest"),
            docker_pull_policy=docker_config.get("pull_policy", "if-not-present"),
            docker_mounts=validated_mounts,
            db_path=data.get("database", {}).get("path", "tasks.db"),
            log_level=data.get("logging", {}).get("level", "INFO"),
            log_console=data.get("logging", {}).get("console", True),
            log_file=data.get("logging", {}).get("file", True),
            log_file_path=data.get("logging", {}).get("file_path", "server.log"),
        )

    @staticmethod
    def _validate_docker_mounts(mounts: object) -> list[str]:
        if not isinstance(mounts, list):
            raise ValueError("Invalid docker mounts: expected a list")

        validated_mounts: list[str] = []
        for mount in mounts:
            if not isinstance(mount, str):
                raise ValueError("Invalid docker mount format")

            parts = mount.split(":")
            if len(parts) not in (2, 3):
                raise ValueError("Invalid docker mount format")

            host_path, container_path = parts[0], parts[1]
            if not host_path or not container_path:
                raise ValueError("Invalid docker mount format")

            if not Path(host_path).is_absolute():
                raise ValueError("Docker mount host path must be absolute")

            if not Path(container_path).is_absolute():
                raise ValueError("Docker mount container path must be absolute")

            if len(parts) == 3:
                Config._validate_docker_mount_mode(parts[2])

            validated_mounts.append(mount)

        return validated_mounts

    @staticmethod
    def _validate_docker_mount_mode(mode: str) -> None:
        if not mode:
            raise ValueError("Invalid docker mount mode")

        options = mode.split(",")
        if any(not option for option in options):
            raise ValueError("Invalid docker mount mode")

        allowed_options = {"ro", "rw", "z", "Z"}
        if any(option not in allowed_options for option in options):
            raise ValueError("Invalid docker mount mode")

        if "ro" in options and "rw" in options:
            raise ValueError("Invalid docker mount mode")

    def ensure_workspace_structure(self):
        """Create workspace directory structure if it doesn't exist"""
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "repos").mkdir(exist_ok=True)
        (self.workspace_path / "logs").mkdir(exist_ok=True)

    def get_db_full_path(self) -> Path:
        """Get full path to database file"""
        db_path = Path(self.db_path)
        if db_path.is_absolute():
            return db_path
        return self.workspace_path / self.db_path
