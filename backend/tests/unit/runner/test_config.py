import os
import pytest
from runner.config import RunnerConfig


@pytest.fixture
def required_env(monkeypatch):
    monkeypatch.setenv("RUNNER_SERVER_URL", "http://localhost:8080")
    monkeypatch.setenv("RUNNER_ID", "test-runner")
    monkeypatch.setenv("RUNNER_TOKEN", "token")


def test_runner_config_crates_io_defaults(required_env):
    config = RunnerConfig.from_env()
    assert config.crates_io_user_agent == "crateprobe-runner"
    assert config.crates_io_rate_limit_rps == 1.0
    assert config.crates_io_cache_ttl_seconds == 300
    assert config.crates_io_max_concurrent_downloads == config.max_jobs


def test_runner_config_crates_io_from_env(required_env, monkeypatch):
    monkeypatch.setenv("CRATES_IO_USER_AGENT", "my-bot")
    monkeypatch.setenv("CRATES_IO_RATE_LIMIT_RPS", "2.5")
    monkeypatch.setenv("CRATES_IO_CACHE_TTL_SECONDS", "600")
    monkeypatch.setenv("CRATES_IO_MAX_CONCURRENT_DOWNLOADS", "5")
    config = RunnerConfig.from_env()
    assert config.crates_io_user_agent == "my-bot"
    assert config.crates_io_rate_limit_rps == 2.5
    assert config.crates_io_cache_ttl_seconds == 600
    assert config.crates_io_max_concurrent_downloads == 5
