# Docker 执行器实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 添加 Docker 作为第三种任务执行模式，支持自定义镜像和资源限制

**Architecture:** 新增 DockerRunner 类管理容器生命周期，Config 添加 execution_mode 配置，TaskExecutor 根据配置选择执行方式

**Tech Stack:** Python, Docker SDK (docker-py), FastAPI

---

## Task 1: 更新配置类添加 Docker 支持

**Files:**
- Modify: `backend/app/config.py`
- Test: `backend/tests/unit/test_config.py`

**Step 1: 编写测试验证新配置字段**

在 `backend/tests/unit/test_config.py` 添加：

```python
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

    with tempfile.NamedTemporaryFile(mode='wb', delete=False, suffix='.toml') as f:
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
```

**Step 2: 运行测试确认失败**

```bash
cd /home/wizeaz/exp-plat/backend
uv run pytest tests/unit/test_config.py::test_config_loads_docker_settings -v
```

Expected: FAIL - AttributeError for execution_mode, max_cpus, docker_image, docker_pull_policy

**Step 3: 实现配置类更新**

修改 `backend/app/config.py`：

```python
@dataclass
class Config:
    """Application configuration"""
    server_port: int = 8000
    server_host: str = "0.0.0.0"
    workspace_path: Path = Path("./workspace")
    max_jobs: int = 3
    max_memory_gb: int = 20
    max_runtime_hours: int = 24
    max_cpus: int = 4  # 新增
    use_systemd: bool = True
    execution_mode: str = "systemd"  # 新增：systemd, resource, docker
    docker_image: str = "rust-cargo-rapx:latest"  # 新增
    docker_pull_policy: str = "if-not-present"  # 新增
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

        return cls(
            server_port=data.get("server", {}).get("port", 8000),
            server_host=data.get("server", {}).get("host", "0.0.0.0"),
            workspace_path=Path(data.get("workspace", {}).get("path", "./workspace")),
            max_jobs=execution.get("max_jobs", 3),
            max_memory_gb=execution.get("max_memory_gb", 20),
            max_runtime_hours=execution.get("max_runtime_hours", 24),
            max_cpus=execution.get("max_cpus", 4),
            use_systemd=execution.get("use_systemd", True),
            execution_mode=execution.get("execution_mode", "systemd"),
            docker_image=docker_config.get("image", "rust-cargo-rapx:latest"),
            docker_pull_policy=docker_config.get("pull_policy", "if-not-present"),
            db_path=data.get("database", {}).get("path", "tasks.db"),
            log_level=data.get("logging", {}).get("level", "INFO"),
            log_console=data.get("logging", {}).get("console", True),
            log_file=data.get("logging", {}).get("file", True),
            log_file_path=data.get("logging", {}).get("file_path", "server.log"),
        )
```

**Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_config.py::test_config_loads_docker_settings -v
```

Expected: PASS

**Step 5: 提交**

```bash
git add backend/app/config.py backend/tests/unit/test_config.py
git commit -m "feat: add Docker execution configuration options

- Add execution_mode field (systemd/resource/docker)
- Add max_cpus for CPU limiting
- Add docker_image and docker_pull_policy settings"
```

---

## Task 2: 创建 DockerRunner 类

**Files:**
- Create: `backend/app/utils/docker_runner.py`
- Create: `backend/tests/unit/test_docker_runner.py`

**Step 1: 编写 DockerRunner 测试**

创建 `backend/tests/unit/test_docker_runner.py`：

```python
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, AsyncMock
from app.utils.docker_runner import DockerRunner, ExecutionResult


@pytest.fixture
def docker_runner():
    return DockerRunner(
        image="rust:test",
        max_memory_gb=8,
        max_runtime_hours=2,
        max_cpus=2
    )


def test_docker_runner_initialization(docker_runner):
    assert docker_runner.image == "rust:test"
    assert docker_runner.max_memory_gb == 8
    assert docker_runner.max_runtime_hours == 2
    assert docker_runner.max_cpus == 2


@pytest.mark.asyncio
async def test_run_builds_correct_command(docker_runner):
    """Test that Docker command is built with correct resource limits"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_container = Mock()
        mock_container.wait.return_value = {'StatusCode': 0}
        mock_container.logs.return_value = b"test output"
        mock_client.containers.run.return_value = mock_container
        mock_docker.return_value = mock_client

        workspace = Path("/tmp/workspace")
        stdout_log = Path("/tmp/stdout.log")
        stderr_log = Path("/tmp/stderr.log")

        result = await docker_runner.run(
            command=["cargo", "rapx"],
            workspace_dir=workspace,
            stdout_log=stdout_log,
            stderr_log=stderr_log
        )

        # Verify container was created with correct parameters
        call_kwargs = mock_client.containers.run.call_args[1]
        assert call_kwargs['image'] == "rust:test"
        assert call_kwargs['mem_limit'] == "8g"
        assert call_kwargs['cpu_quota'] == 200000  # 2 CPUs
        assert call_kwargs['command'] == ["cargo", "rapx"]


def test_ensure_image_with_if_not_present_policy(docker_runner):
    """Test image check with if-not-present policy"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_client.images.list.return_value = [Mock(tags=["rust:test"])]
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True


def test_ensure_image_pulls_when_missing(docker_runner):
    """Test image is pulled when not present"""
    with patch('docker.from_env') as mock_docker:
        mock_client = Mock()
        mock_client.images.list.return_value = []
        mock_client.images.pull.return_value = Mock()
        mock_docker.return_value = mock_client

        result = docker_runner.ensure_image("if-not-present")
        assert result is True
        mock_client.images.pull.assert_called_once_with("rust:test")
```

**Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/test_docker_runner.py -v
```

Expected: FAIL - ModuleNotFoundError

**Step 3: 安装 docker-py 依赖**

检查当前依赖并添加 docker：

```bash
cd /home/wizeaz/exp-plat/backend
cat pyproject.toml | grep -A 20 "dependencies"
```

编辑 `pyproject.toml`，在 dependencies 中添加 `docker>=7.0.0`：

```toml
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "websockets>=12.0",
    "aiofiles>=23.2.0",
    "httpx>=0.25.0",
    "tomli>=2.0.1;python_version<'3.11'",
    "docker>=7.0.0",  # 新增
]
```

更新依赖：

```bash
uv sync
```

**Step 4: 实现 DockerRunner 类**

创建 `backend/app/utils/docker_runner.py`：

```python
import asyncio
import shutil
from pathlib import Path
from typing import List, Optional
from dataclasses import dataclass
import docker
from docker.errors import ImageNotFound, APIError


@dataclass
class ExecutionResult:
    """Result of container execution"""
    exit_code: int
    stdout: str
    stderr: str


class DockerRunner:
    """Execute tasks in Docker containers with resource limits"""

    def __init__(
        self,
        image: str,
        max_memory_gb: int,
        max_runtime_hours: int,
        max_cpus: int
    ):
        self.image = image
        self.max_memory_gb = max_memory_gb
        self.max_runtime_hours = max_runtime_hours
        self.max_cpus = max_cpus
        self._client: Optional[docker.DockerClient] = None

    @property
    def client(self) -> docker.DockerClient:
        """Lazy initialization of Docker client"""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def ensure_image(self, pull_policy: str = "if-not-present") -> bool:
        """
        Ensure the Docker image is available locally.

        Args:
            pull_policy: "always", "if-not-present", or "never"

        Returns:
            True if image is available, False otherwise
        """
        if pull_policy == "always":
            self.client.images.pull(self.image)
            return True

        if pull_policy == "never":
            try:
                self.client.images.get(self.image)
                return True
            except ImageNotFound:
                return False

        # if-not-present: pull only if not available
        try:
            self.client.images.get(self.image)
            return True
        except ImageNotFound:
            try:
                self.client.images.pull(self.image)
                return True
            except APIError as e:
                raise RuntimeError(f"Failed to pull image {self.image}: {e}")

    def _build_resource_limits(self) -> dict:
        """Build Docker resource limit parameters"""
        # CPU quota: number of CPUs * 100000 (microseconds per period)
        cpu_quota = int(self.max_cpus * 100000)

        return {
            "mem_limit": f"{self.max_memory_gb}g",
            "memswap_limit": f"{self.max_memory_gb}g",  # Disable swap
            "cpu_quota": cpu_quota,
            "cpu_period": 100000,
            "stop_timeout": self.max_runtime_hours * 3600,
        }

    async def run(
        self,
        command: List[str],
        workspace_dir: Path,
        stdout_log: Path,
        stderr_log: Path
    ) -> int:
        """
        Run a command in a Docker container with resource limits.

        Args:
            command: Command and arguments to execute
            workspace_dir: Host path to mount as /workspace in container
            stdout_log: Path to write stdout
            stderr_log: Path to write stderr

        Returns:
            Container exit code
        """
        # Ensure workspace directory exists
        workspace_dir.mkdir(parents=True, exist_ok=True)
        stdout_log.parent.mkdir(parents=True, exist_ok=True)
        stderr_log.parent.mkdir(parents=True, exist_ok=True)

        # Build resource limits
        resource_limits = self._build_resource_limits()

        # Prepare volume mounts
        volumes = {
            str(workspace_dir.resolve()): {
                "bind": "/workspace",
                "mode": "rw"
            }
        }

        # Run container
        try:
            container = self.client.containers.run(
                image=self.image,
                command=command,
                working_dir="/workspace",
                volumes=volumes,
                detach=True,
                stdout=True,
                stderr=True,
                **resource_limits
            )

            # Wait for container with timeout
            timeout_seconds = self.max_runtime_hours * 3600
            try:
                result = container.wait(timeout=timeout_seconds)
                exit_code = result.get("StatusCode", -1)
            except Exception:
                # Timeout or error - stop the container
                container.stop(timeout=10)
                exit_code = -1

            # Get logs
            logs = container.logs(stdout=True, stderr=False).decode('utf-8', errors='replace')
            stderr_logs = container.logs(stdout=False, stderr=True).decode('utf-8', errors='replace')

            # Write logs to files
            stdout_log.write_text(logs)
            stderr_log.write_text(stderr_logs)

            # Cleanup
            container.remove(force=True)

            return exit_code

        except APIError as e:
            # Write error to stderr log
            stderr_log.write_text(f"Docker API error: {e}")
            return -1
        except Exception as e:
            stderr_log.write_text(f"Unexpected error: {e}")
            return -1

    def is_available(self) -> bool:
        """Check if Docker is available on this system"""
        if not shutil.which("docker"):
            return False

        try:
            self.client.ping()
            return True
        except Exception:
            return False
```

**Step 5: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_docker_runner.py -v
```

Expected: PASS

**Step 6: 提交**

```bash
git add backend/app/utils/docker_runner.py backend/tests/unit/test_docker_runner.py backend/pyproject.toml
git commit -m "feat: add DockerRunner class for container-based execution

- Create DockerRunner with resource limit support
- Support memory, CPU, and timeout constraints
- Add image pull policy management
- Include availability checking"
```

---

## Task 3: 修改 TaskExecutor 支持 Docker 模式

**Files:**
- Modify: `backend/app/services/task_executor.py`
- Modify: `backend/app/utils/__init__.py` (if exists, or create exports)

**Step 1: 编写集成测试**

在 `backend/tests/unit/test_task_executor.py` 添加（或创建该文件）：

```python
import pytest
from unittest.mock import Mock, patch, AsyncMock
from pathlib import Path
from app.services.task_executor import TaskExecutor
from app.config import Config


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
    with patch('app.services.task_executor.DockerRunner') as mock_runner_class:
        mock_runner = Mock()
        mock_runner.is_available.return_value = True
        mock_runner.run = AsyncMock(return_value=0)
        mock_runner_class.return_value = mock_runner

        executor = TaskExecutor(mock_config, mock_database)

        # Verify DockerRunner was initialized
        mock_runner_class.assert_called_once_with(
            image="rust:test",
            max_memory_gb=8,
            max_runtime_hours=2,
            max_cpus=4
        )
```

**Step 2: 运行测试确认失败**

```bash
uv run pytest tests/unit/test_task_executor.py::test_task_executor_uses_docker_when_configured -v
```

Expected: FAIL

**Step 3: 修改 TaskExecutor**

编辑 `backend/app/services/task_executor.py`：

```python
import asyncio
import shutil
import tarfile
from pathlib import Path
from datetime import datetime
from typing import Tuple
from app.config import Config
from app.database import Database
from app.models import TaskStatus
from app.services.crates_api import CratesAPI
from app.utils.resource_limit import ResourceLimiter
from app.utils.docker_runner import DockerRunner


class TaskExecutor:
    """Executes individual tasks"""

    def __init__(self, config: Config, database: Database):
        self.config = config
        self.db = database
        self.crates_api = CratesAPI()

        # Initialize appropriate runner based on execution mode
        self.execution_mode = getattr(config, 'execution_mode', 'systemd')

        if self.execution_mode == "docker":
            self.docker_runner = DockerRunner(
                image=config.docker_image,
                max_memory_gb=config.max_memory_gb,
                max_runtime_hours=config.max_runtime_hours,
                max_cpus=getattr(config, 'max_cpus', 4)
            )
            self.limiter = None
        else:
            self.docker_runner = None
            self.limiter = ResourceLimiter(
                use_systemd=config.use_systemd,
                max_memory_gb=config.max_memory_gb,
                max_runtime_hours=config.max_runtime_hours
            )

    async def prepare_workspace(self, task_id: int, crate_name: str, version: str) -> Path:
        """Download and extract crate to workspace"""
        workspace_dir = self.config.workspace_path / "repos" / f"{crate_name}-{version}"

        # If workspace directory already exists (e.g., from retry), clean it first
        if workspace_dir.exists():
            shutil.rmtree(workspace_dir)

        workspace_dir.mkdir(parents=True, exist_ok=True)

        # Download crate file
        crate_file = self.config.workspace_path / "repos" / f"{crate_name}-{version}.crate"

        # Remove old crate file if it exists
        if crate_file.exists():
            crate_file.unlink()

        await self.crates_api.download_crate(crate_name, version, str(crate_file))

        # Extract crate - .crate files contain a top-level directory we need to strip
        temp_extract_dir = self.config.workspace_path / "repos" / f"_temp_{crate_name}-{version}"
        temp_extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            with tarfile.open(crate_file, "r:gz") as tar:
                tar.extractall(temp_extract_dir)

            # Move contents from the inner directory to workspace_dir
            # .crate files have structure: crate-name-version/...
            inner_dir = temp_extract_dir / f"{crate_name}-{version}"
            if inner_dir.exists():
                # Move all contents from inner_dir to workspace_dir
                for item in inner_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
            else:
                # Fallback: if structure is different, move everything
                for item in temp_extract_dir.iterdir():
                    shutil.move(str(item), str(workspace_dir))
        finally:
            # Clean up temp directory
            if temp_extract_dir.exists():
                shutil.rmtree(temp_extract_dir)

        # Remove crate file after extraction
        if crate_file.exists():
            crate_file.unlink()

        return workspace_dir

    async def execute_task(self, task_id: int):
        """Execute a single task"""
        task = self.db.get_task(task_id)
        if not task:
            return

        try:
            # Update status to running
            self.db.update_task_status(task_id, TaskStatus.RUNNING, started_at=datetime.now())

            # Prepare workspace
            workspace_dir = await self.prepare_workspace(task_id, task.crate_name, task.version)

            # Ensure log directory exists
            Path(task.stdout_log).parent.mkdir(parents=True, exist_ok=True)
            Path(task.stderr_log).parent.mkdir(parents=True, exist_ok=True)

            if self.execution_mode == "docker":
                # Use Docker for execution
                if not self.docker_runner.is_available():
                    raise RuntimeError("Docker is not available but execution_mode is 'docker'")

                # Ensure image is available
                if not self.docker_runner.ensure_image(self.config.docker_pull_policy):
                    raise RuntimeError(f"Docker image {self.config.docker_image} is not available")

                # Run in Docker
                exit_code = await self.docker_runner.run(
                    command=["cargo", "rapx", "-testgen", f"-test-crate={task.crate_name}"],
                    workspace_dir=workspace_dir,
                    stdout_log=Path(task.stdout_log),
                    stderr_log=Path(task.stderr_log)
                )

                # Final count of generated items
                case_count, poc_count = self.count_generated_items(workspace_dir)
                self.db.update_task_counts(task_id, case_count, poc_count)

                # Update final status
                if exit_code == 0:
                    self.db.update_task_status(
                        task_id,
                        TaskStatus.COMPLETED,
                        finished_at=datetime.now(),
                        exit_code=exit_code
                    )
                elif exit_code == 137:
                    # Docker OOM exit code
                    self.db.update_task_status(
                        task_id,
                        TaskStatus.OOM,
                        finished_at=datetime.now(),
                        exit_code=exit_code
                    )
                elif exit_code == -1:
                    # Timeout or Docker error
                    self.db.update_task_status(
                        task_id,
                        TaskStatus.TIMEOUT,
                        finished_at=datetime.now(),
                        exit_code=exit_code
                    )
                else:
                    self.db.update_task_status(
                        task_id,
                        TaskStatus.FAILED,
                        finished_at=datetime.now(),
                        exit_code=exit_code
                    )
            else:
                # Use traditional execution with systemd/resource
                await self._execute_with_limiter(task_id, workspace_dir, task)

        except Exception as e:
            self.db.update_task_status(
                task_id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                error_message=str(e)
            )

    async def _execute_with_limiter(self, task_id: int, workspace_dir: Path, task):
        """Execute task using systemd/resource limiter (original implementation)"""
        # Build command
        cmd = self.limiter.build_command(
            ["cargo", "rapx", "-testgen", f"-test-crate={task.crate_name}"],
            cwd=str(workspace_dir)
        )

        # Open log files
        stdout_log = open(task.stdout_log, "w")
        stderr_log = open(task.stderr_log, "w")

        # Start process
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=stdout_log,
            stderr=stderr_log,
            cwd=workspace_dir,
            preexec_fn=self.limiter.apply_resource_limits if self.limiter.get_limit_method().value == "resource" else None
        )

        # Store PID
        self.db.update_task_pid(task_id, process.pid)

        # Wait for completion with periodic stats updates
        await self._wait_with_stats_updates(process, task_id, workspace_dir)

        stdout_log.close()
        stderr_log.close()

        # Final count of generated items
        case_count, poc_count = self.count_generated_items(workspace_dir)
        self.db.update_task_counts(task_id, case_count, poc_count)

        # Update final status
        if process.returncode == 0:
            self.db.update_task_status(
                task_id,
                TaskStatus.COMPLETED,
                finished_at=datetime.now(),
                exit_code=process.returncode
            )
        else:
            self.db.update_task_status(
                task_id,
                TaskStatus.FAILED,
                finished_at=datetime.now(),
                exit_code=process.returncode
            )

    async def _wait_with_stats_updates(self, process, task_id: int, workspace_dir: Path):
        """Wait for process completion while periodically updating stats in database"""
        update_interval = 10  # Update every 10 seconds

        while True:
            try:
                # Wait for process with timeout
                await asyncio.wait_for(process.wait(), timeout=update_interval)
                # Process completed
                break
            except asyncio.TimeoutError:
                # Process still running, update stats
                case_count, poc_count = self.count_generated_items(workspace_dir)
                self.db.update_task_counts(task_id, case_count, poc_count)
                # Continue waiting

    def count_generated_items(self, workspace_dir: Path) -> Tuple[int, int]:
        """Count generated test cases and POCs"""
        # Now that we've fixed extraction, testgen should be directly in workspace_dir
        testgen_dir = workspace_dir / "testgen"

        case_count = 0
        poc_count = 0

        tests_dir = testgen_dir / "tests"
        if tests_dir.exists():
            case_count = len([d for d in tests_dir.iterdir() if d.is_dir()])

        poc_dir = testgen_dir / "poc"
        if poc_dir.exists():
            poc_count = len([d for d in poc_dir.iterdir() if d.is_dir()])

        return case_count, poc_count
```

**Step 4: 运行测试确认通过**

```bash
uv run pytest tests/unit/test_task_executor.py -v
```

Expected: PASS

**Step 5: 提交**

```bash
git add backend/app/services/task_executor.py backend/tests/unit/test_task_executor.py
git commit -m "feat: integrate Docker execution into TaskExecutor

- Add Docker mode alongside systemd/resource modes
- Handle Docker-specific exit codes (OOM=137)
- Extract limiter execution to separate method"
```

---

## Task 4: 创建 Dockerfile.executor

**Files:**
- Create: `backend/docker/Dockerfile.executor`

**Step 1: 创建 Dockerfile**

```bash
mkdir -p /home/wizeaz/exp-plat/backend/docker
```

创建 `backend/docker/Dockerfile.executor`：

```dockerfile
# Rust + cargo-rapx 执行镜像
FROM rust:1.75-slim-bookworm

# 安装必要的系统依赖
RUN apt-get update && apt-get install -y \
    pkg-config \
    libssl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

# 安装 cargo-rapx
# 注意：根据实际情况可能需要从源码构建或使用特定版本
RUN cargo install cargo-rapx

# 创建工作目录
WORKDIR /workspace

# 默认命令显示帮助
CMD ["cargo", "rapx", "--help"]
```

**Step 2: 验证 Dockerfile 语法**

```bash
cd /home/wizeaz/exp-plat/backend/docker
docker build -f Dockerfile.executor -t rust-cargo-rapx:test .
```

如果 Docker 不可用，跳过此步骤。

**Step 3: 提交**

```bash
git add backend/docker/Dockerfile.executor
git commit -m "feat: add Dockerfile for Rust + cargo-rapx execution environment"
```

---

## Task 5: 更新 config.toml.example

**Files:**
- Modify: `config.toml.example`

**Step 1: 编辑示例配置**

更新 `config.toml.example`，在 `[execution]` 部分添加 Docker 配置：

```toml
# 实验平台统一配置文件示例
# Experiment Platform Unified Configuration Example
#
# 使用方法 (Usage):
#   cp config.toml.example config.toml
#   编辑 config.toml 以适配你的环境

# ============================================
# 后端配置 (Backend Configuration)
# ============================================

[server]
# Web服务端口
# Backend server port
port = 8080

# 绑定地址（0.0.0.0 表示监听所有网络接口）
# Bind address (0.0.0.0 means listen on all network interfaces)
host = "0.0.0.0"

[workspace]
# 工作空间根目录（所有运行时文件都在此目录下）
# Workspace root directory (all runtime files stored here)
path = "./workspace"

[execution]
# 最大并发任务数
# Maximum concurrent tasks
max_jobs = 3

# 单个任务最大内存限制（GB）
# Maximum memory limit per task (GB)
max_memory_gb = 20

# 单个任务最长运行时间（小时）
# Maximum runtime per task (hours)
max_runtime_hours = 24

# 任务执行模式：systemd, resource, docker
# Task execution mode
execution_mode = "systemd"

# 单个任务最大CPU核心数（Docker模式生效）
# Maximum CPU cores per task (effective in Docker mode)
max_cpus = 4

# Docker 配置（当 execution_mode = "docker" 时生效）
[execution.docker]
# Docker 镜像名称
# Docker image name
image = "rust-cargo-rapx:latest"

# 镜像拉取策略：always, if-not-present, never
# Image pull policy
pull_policy = "if-not-present"

[database]
# SQLite数据库路径（相对于workspace或绝对路径）
# SQLite database path (relative to workspace or absolute)
path = "tasks.db"

[logging]
# 日志级别：DEBUG, INFO, WARNING, ERROR
# Log level: DEBUG, INFO, WARNING, ERROR
level = "INFO"

# 是否输出到控制台
# Output to console
console = true

# 是否写入文件
# Write to file
file = true

# 日志文件路径
# Log file path
file_path = "server.log"

# ============================================
# 前端配置 (Frontend Configuration)
# ============================================

[frontend]
# 开发服务器端口
# Development server port
dev_port = 5173

# 生产构建输出目录
# Production build output directory
dist_dir = "dist"

# API 代理目标（指向后端服务器）
# API proxy target (points to backend server)
api_proxy_target = "http://localhost:8080"

# WebSocket 代理目标
# WebSocket proxy target
ws_proxy_target = "ws://localhost:8080"
```

**Step 2: 提交**

```bash
git add config.toml.example
git commit -m "docs: update config example with Docker execution settings

- Add execution_mode configuration
- Add max_cpus for CPU limiting
- Add docker configuration section"
```

---

## Task 6: 运行完整测试套件

**Files:**
- Run: All tests

**Step 1: 运行所有单元测试**

```bash
cd /home/wizeaz/exp-plat/backend
uv run pytest tests/unit/ -v
```

Expected: All tests pass

**Step 2: 验证代码导入无错误**

```bash
uv run python -c "from app.services.task_executor import TaskExecutor; from app.utils.docker_runner import DockerRunner; print('All imports OK')"
```

Expected: "All imports OK"

**Step 3: 最终提交**

```bash
git log --oneline -10
```

确认所有提交都已创建。

---

## 实施检查清单

- [ ] Task 1: 配置类更新完成
- [ ] Task 2: DockerRunner 类实现完成
- [ ] Task 3: TaskExecutor Docker 集成完成
- [ ] Task 4: Dockerfile 创建完成
- [ ] Task 5: 示例配置更新完成
- [ ] Task 6: 所有测试通过

## 使用说明

启用 Docker 执行模式：

1. 确保 Docker 已安装并运行
2. 构建或拉取执行镜像：
   ```bash
   cd backend/docker
   docker build -f Dockerfile.executor -t rust-cargo-rapx:latest .
   ```
3. 编辑 `config.toml`：
   ```toml
   [execution]
   execution_mode = "docker"
   max_cpus = 4

   [execution.docker]
   image = "rust-cargo-rapx:latest"
   pull_policy = "if-not-present"
   ```
4. 启动后端服务
