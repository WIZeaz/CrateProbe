# Docker Mounts Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `mounts` configuration option for the docker runner to allow specifying custom container mounts.

**Architecture:** Add `docker_mounts` to `AppConfig`, validate it on load (requiring absolute paths and standard docker bind mount string format), update `DockerRunner` to accept a `mounts` list and inject it alongside the default workspace mount.

**Tech Stack:** Python 3, FastAPI, Pydantic/dataclasses, pytest, docker-py

---

## Chunk 1: Configuration Updates

### Task 1: Update AppConfig and validation

**Files:**
- Modify: `backend/app/config.py`
- Modify: `config.toml.example`
- Modify: `backend/tests/unit/test_config.py`

- [ ] **Step 1: Write test for valid mounts configuration**

```python
# In backend/tests/unit/test_config.py
def test_config_loads_docker_mounts(tmp_path):
    config_file = tmp_path / "config.toml"
    config_file.write_text("""
[server]
host = "127.0.0.1"
port = 8000

[execution]
mode = "docker"

[execution.docker]
image = "rust:test"
pull_policy = "never"
mounts = ["/host/path:/container/path:rw"]
    """)
    
    config = AppConfig.load(config_file)
    assert config.docker_mounts == ["/host/path:/container/path:rw"]
```

- [ ] **Step 2: Write tests for invalid mount formats**

```python
# In backend/tests/unit/test_config.py
def test_config_validates_docker_mounts_format(tmp_path):
    config_file = tmp_path / "config.toml"
    
    # Test invalid format (missing parts)
    config_file.write_text("""
[server]
host = "127.0.0.1"
port = 8000

[execution.docker]
mounts = ["invalid-format"]
    """)
    with pytest.raises(ValueError, match="Invalid mount format"):
        AppConfig.load(config_file)
        
    # Test invalid format (relative host path)
    config_file.write_text("""
[server]
host = "127.0.0.1"
port = 8000

[execution.docker]
mounts = ["relative/path:/container/path:ro"]
    """)
    with pytest.raises(ValueError, match="Host path must be absolute"):
        AppConfig.load(config_file)
        
    # Test invalid format (relative container path)
    config_file.write_text("""
[server]
host = "127.0.0.1"
port = 8000

[execution.docker]
mounts = ["/absolute/path:relative/container:ro"]
    """)
    with pytest.raises(ValueError, match="Container path must be absolute"):
        AppConfig.load(config_file)
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd backend && uv run pytest tests/unit/test_config.py -v`
Expected: FAIL due to missing `docker_mounts` field and validation.

- [ ] **Step 4: Update `AppConfig` implementation**

```python
# In backend/app/config.py
# Add to AppConfig dataclass:
    docker_mounts: list[str] = field(default_factory=list)

# Add validation in `load` method under `if "docker" in execution:`
            docker_mounts = docker_config.get("mounts", [])
            for m in docker_mounts:
                parts = m.split(":")
                if len(parts) not in (2, 3):
                    raise ValueError(f"Invalid mount format: '{m}'. Expected 'host_path:container_path[:mode]'")
                if not parts[0].startswith("/"):
                    raise ValueError(f"Host path must be absolute in mount '{m}'")
                if not parts[1].startswith("/"):
                    raise ValueError(f"Container path must be absolute in mount '{m}'")

# Update return AppConfig(...):
            docker_mounts=docker_mounts,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_config.py -v`
Expected: PASS

- [ ] **Step 6: Update `config.toml.example`**

```toml
# Add under [execution.docker]:
# Optional: List of additional volume mounts. 
# Format: ["/absolute/host/path:/absolute/container/path:mode"]
# Example:
# mounts = [
#     "/tmp/cargo-cache:/usr/local/cargo/registry:rw"
# ]
```

- [ ] **Step 7: Commit changes**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py config.toml.example
git commit -m "feat(config): add and validate docker_mounts option"
```

## Chunk 2: DockerRunner Updates

### Task 2: Update DockerRunner to use mounts

**Files:**
- Modify: `backend/app/utils/docker_runner.py`
- Modify: `backend/tests/unit/test_docker_runner.py`

- [ ] **Step 1: Write test for DockerRunner volumes**

```python
# In backend/tests/unit/test_docker_runner.py
# Update the existing `docker_runner` fixture to accept mounts, or create a test:
async def test_run_builds_correct_volumes(tmp_path):
    with patch("docker.from_env") as mock_docker:
        mock_client = MagicMock()
        mock_container = MagicMock()
        mock_container.wait.return_value = {"StatusCode": 0}
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client
        
        runner = DockerRunner(
            image="rust:test",
            max_memory_gb=8,
            max_runtime_seconds=7200,
            max_cpus=2,
            mounts=["/host/data:/data:ro"]
        )
        
        workspace_dir = tmp_path / "workspace"
        stdout_log = tmp_path / "stdout.log"
        stderr_log = tmp_path / "stderr.log"
        
        await runner.run(["echo", "hi"], workspace_dir, stdout_log, stderr_log)
        
        _, kwargs = mock_client.containers.run.call_args
        volumes = kwargs.get("volumes", [])
        
        assert isinstance(volumes, list)
        assert f"{workspace_dir.resolve()}:/workspace:rw" in volumes
        assert "/host/data:/data:ro" in volumes
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_docker_runner.py::test_run_builds_correct_volumes -v`
Expected: FAIL due to unexpected keyword argument `mounts` and dict format for volumes.

- [ ] **Step 3: Update `DockerRunner` implementation**

```python
# In backend/app/utils/docker_runner.py
# Update __init__:
    def __init__(
        self, image: str, max_memory_gb: int, max_runtime_seconds: int, max_cpus: int, mounts: List[str] = None
    ):
        self.image = image
        self.max_memory_gb = max_memory_gb
        self.max_runtime_seconds = max_runtime_seconds
        self.max_cpus = max_cpus
        self.mounts = mounts or []
        self._client: Optional[docker.DockerClient] = None

# Update run() method volumes list:
        # Prepare volume mounts (using list format)
        volumes = [f"{workspace_dir.resolve()}:/workspace:rw"] + self.mounts
```

- [ ] **Step 4: Fix existing tests**

Run: `cd backend && uv run pytest tests/unit/test_docker_runner.py -v`
There might be type issues if previous tests mocked `volumes` as dict. Update any existing test in `test_docker_runner.py` that asserts on the `volumes` structure to expect a list instead of a dict.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_docker_runner.py -v`
Expected: PASS

- [ ] **Step 6: Commit changes**

```bash
git add backend/app/utils/docker_runner.py backend/tests/unit/test_docker_runner.py
git commit -m "feat(runner): support custom mounts in DockerRunner"
```

## Chunk 3: TaskExecutor Updates

### Task 3: Pass config mounts to DockerRunner

**Files:**
- Modify: `backend/app/services/task_executor.py`
- Modify: `backend/tests/unit/test_task_executor.py`

- [ ] **Step 1: Write test for TaskExecutor passing mounts**

```python
# In backend/tests/unit/test_task_executor.py
async def test_task_executor_passes_mounts_to_docker(mock_config, mock_database):
    mock_config.execution_mode = "docker"
    mock_config.docker_image = "rust:test"
    mock_config.docker_pull_policy = "if-not-present"
    mock_config.docker_mounts = ["/host:/container:rw"]
    
    with patch("app.services.task_executor.DockerRunner") as MockRunner:
        executor = TaskExecutor(mock_config, mock_database)
        
        # Verify DockerRunner was instantiated with correct mounts
        MockRunner.assert_called_once()
        _, kwargs = MockRunner.call_args
        assert kwargs.get("mounts") == ["/host:/container:rw"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_task_executor.py::test_task_executor_passes_mounts_to_docker -v`
Expected: FAIL due to missing `mounts` argument in `DockerRunner` instantiation.

- [ ] **Step 3: Update `TaskExecutor` implementation**

```python
# In backend/app/services/task_executor.py
# Update DockerRunner instantiation in __init__:
            self.docker_runner = DockerRunner(
                image=config.docker_image,
                max_memory_gb=config.max_memory_gb,
                max_runtime_seconds=config.max_runtime_hours * 3600,
                max_cpus=config.max_jobs,
                mounts=config.docker_mounts,
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && uv run pytest tests/unit/test_task_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit changes**

```bash
git add backend/app/services/task_executor.py backend/tests/unit/test_task_executor.py
git commit -m "feat(executor): pass docker_mounts from config to DockerRunner"
```
