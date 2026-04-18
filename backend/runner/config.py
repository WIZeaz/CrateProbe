import os
from dataclasses import dataclass, field
from typing import List


@dataclass
class RunnerConfig:
    server_url: str
    runner_id: str
    runner_token: str
    poll_interval_seconds: float = 3.0
    metrics_interval_seconds: float = 10.0
    request_timeout_seconds: float = 10.0
    max_jobs: int = 3
    max_memory_gb: int = 20
    max_runtime_seconds: int = 86400
    max_cpus: int = 4
    docker_image: str = "rust-cargo-rapx:latest"
    docker_pull_policy: str = "if-not-present"
    docker_mounts: List[str] = field(default_factory=list)
    workspace_dir: str = "/workspace"

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        server_url = os.environ.get("RUNNER_SERVER_URL")
        runner_id = os.environ.get("RUNNER_ID")
        runner_token = os.environ.get("RUNNER_TOKEN")
        poll_interval_raw = os.environ.get("RUNNER_POLL_INTERVAL_SECONDS", "3")
        metrics_interval_raw = os.environ.get("RUNNER_METRICS_INTERVAL_SECONDS", "10")
        request_timeout_raw = os.environ.get("RUNNER_REQUEST_TIMEOUT_SECONDS", "10")
        max_jobs_raw = os.environ.get("RUNNER_MAX_JOBS", "3")
        max_memory_raw = os.environ.get("RUNNER_MAX_MEMORY_GB", "20")
        max_runtime_raw = os.environ.get("RUNNER_MAX_RUNTIME_SECONDS", "86400")
        max_cpus_raw = os.environ.get("RUNNER_MAX_CPUS", "4")
        docker_image = os.environ.get("RUNNER_DOCKER_IMAGE", "rust-cargo-rapx:latest")
        docker_pull_policy = os.environ.get(
            "RUNNER_DOCKER_PULL_POLICY", "if-not-present"
        )
        mounts_raw = os.environ.get("RUNNER_DOCKER_MOUNTS", "")
        workspace_dir = os.environ.get("RUNNER_WORKSPACE_DIR", "/workspace")

        missing = []
        if not server_url:
            missing.append("RUNNER_SERVER_URL")
        if not runner_id:
            missing.append("RUNNER_ID")
        if not runner_token:
            missing.append("RUNNER_TOKEN")
        if missing:
            raise ValueError(
                f"Missing required runner environment variables: {', '.join(missing)}"
            )

        def _float(name: str, raw: str) -> float:
            try:
                return float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc

        def _int(name: str, raw: str) -> int:
            try:
                return int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc

        docker_mounts = [m.strip() for m in mounts_raw.split(",") if m.strip()]

        max_jobs = _int("RUNNER_MAX_JOBS", max_jobs_raw)
        if max_jobs < 1:
            raise ValueError("RUNNER_MAX_JOBS must be >= 1")

        return cls(
            server_url=server_url,
            runner_id=runner_id,
            runner_token=runner_token,
            poll_interval_seconds=_float(
                "RUNNER_POLL_INTERVAL_SECONDS", poll_interval_raw
            ),
            metrics_interval_seconds=_float(
                "RUNNER_METRICS_INTERVAL_SECONDS", metrics_interval_raw
            ),
            request_timeout_seconds=_float(
                "RUNNER_REQUEST_TIMEOUT_SECONDS", request_timeout_raw
            ),
            max_jobs=max_jobs,
            max_memory_gb=_int("RUNNER_MAX_MEMORY_GB", max_memory_raw),
            max_runtime_seconds=_int("RUNNER_MAX_RUNTIME_SECONDS", max_runtime_raw),
            max_cpus=_int("RUNNER_MAX_CPUS", max_cpus_raw),
            docker_image=docker_image,
            docker_pull_policy=docker_pull_policy,
            docker_mounts=docker_mounts,
            workspace_dir=workspace_dir,
        )
