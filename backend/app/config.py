import sys
from pathlib import Path
from dataclasses import dataclass, field

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@dataclass
class Config:
    server_port: int = 8000
    server_host: str = "0.0.0.0"
    workspace_path: Path = Path("./workspace")
    db_path: str = "tasks.db"
    log_level: str = "INFO"
    log_console: bool = True
    log_file: bool = True
    log_file_path: str = "server.log"
    lease_ttl_seconds: int = 30
    runner_offline_seconds: int = 30
    admin_token: str = ""

    @classmethod
    def from_file(cls, path: str) -> "Config":
        config_path = Path(path)
        if not config_path.exists():
            return cls()

        with open(config_path, "rb") as f:
            data = tomllib.load(f)

        server = data.get("server", {})
        workspace = data.get("workspace", {})
        database = data.get("database", {})
        logging_cfg = data.get("logging", {})
        distributed = data.get("distributed", {})
        security = data.get("security", {})

        return cls(
            server_port=server.get("port", 8000),
            server_host=server.get("host", "0.0.0.0"),
            workspace_path=Path(workspace.get("path", "./workspace")),
            db_path=database.get("path", "tasks.db"),
            log_level=logging_cfg.get("level", "INFO"),
            log_console=logging_cfg.get("console", True),
            log_file=logging_cfg.get("file", True),
            log_file_path=logging_cfg.get("file_path", "server.log"),
            lease_ttl_seconds=distributed.get("lease_ttl_seconds", 30),
            runner_offline_seconds=distributed.get("runner_offline_seconds", 30),
            admin_token=security.get("admin_token", ""),
        )

    def ensure_workspace_structure(self):
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        (self.workspace_path / "repos").mkdir(exist_ok=True)
        (self.workspace_path / "logs").mkdir(exist_ok=True)

    def get_db_full_path(self) -> Path:
        db_path = Path(self.db_path)
        if db_path.is_absolute():
            return db_path
        return self.workspace_path / self.db_path
