import os
from dataclasses import dataclass


@dataclass
class RunnerConfig:
    server_url: str
    runner_id: str
    runner_token: str
    poll_interval_seconds: float = 3.0
    request_timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "RunnerConfig":
        server_url = os.environ.get("RUNNER_SERVER_URL")
        runner_id = os.environ.get("RUNNER_ID")
        runner_token = os.environ.get("RUNNER_TOKEN")
        poll_interval_raw = os.environ.get("RUNNER_POLL_INTERVAL_SECONDS", "3")

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

        try:
            poll_interval_seconds = float(poll_interval_raw)
        except ValueError as exc:
            raise ValueError("RUNNER_POLL_INTERVAL_SECONDS must be a number") from exc

        if poll_interval_seconds <= 0:
            raise ValueError("RUNNER_POLL_INTERVAL_SECONDS must be > 0")

        return cls(
            server_url=server_url,
            runner_id=runner_id,
            runner_token=runner_token,
            poll_interval_seconds=poll_interval_seconds,
        )
