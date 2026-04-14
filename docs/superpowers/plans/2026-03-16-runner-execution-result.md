# Runner Execution Result Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 Runner 返回机制，使其返回结构化的 ExecutionResult（包含 state、exit_code、message），而非单纯的 exit_code

**Architecture：** 定义 Runner 抽象基类和 ExecutionResult dataclass，DockerRunner 和新建的 LocalRunner 统一实现 run() 接口，TaskExecutor 简化为直接读取 result.state 和 result.message

**Tech Stack：** Python/FastAPI, SQLModel, Vue 3, pytest, Black

---

## 文件结构概览

| 文件 | 类型 | 职责 |
|------|------|------|
| `backend/app/utils/runner_base.py` | 新建 | ExecutionResult dataclass 和 Runner ABC |
| `backend/app/utils/local_runner.py` | 新建 | 本地执行 Runner（封装 systemd/resource 逻辑） |
| `backend/app/utils/docker_runner.py` | 修改 | 继承 Runner ABC，返回 ExecutionResult |
| `backend/app/utils/resource_limit.py` | 修改 | 可能需要调整接口暴露 |
| `backend/app/services/task_executor.py` | 修改 | 简化逻辑，使用新的 Runner 接口 |
| `backend/app/database.py` | 修改 | TaskRecord 新增 message 字段 |
| `backend/app/models.py` | 修改 | TaskDetail 新增 message 字段 |
| `backend/tests/unit/test_runner_base.py` | 新建 | ExecutionResult 基础测试 |
| `backend/tests/unit/test_local_runner.py` | 新建 | LocalRunner 测试 |
| `backend/tests/unit/test_docker_runner.py` | 修改 | 更新测试以验证 ExecutionResult |
| `frontend/src/views/TaskDetail.vue` | 修改 | 展示 message 字段 |

---

## Chunk 1: 基础数据结构 (runner_base.py)

### Task 1: 创建 ExecutionResult 和 Runner ABC

**Files:**
- Create: `backend/app/utils/runner_base.py`
- Test: `backend/tests/unit/test_runner_base.py`

- [ ] **Step 1: 编写 ExecutionResult 测试**

```python
# backend/tests/unit/test_runner_base.py
import pytest
from app.utils.runner_base import ExecutionResult
from app.models import TaskStatus


def test_execution_result_creation():
    """Test ExecutionResult can be created with all fields"""
    result = ExecutionResult(
        state=TaskStatus.COMPLETED,
        exit_code=0,
        message="Completed successfully"
    )
    assert result.state == TaskStatus.COMPLETED
    assert result.exit_code == 0
    assert result.message == "Completed successfully"


def test_execution_result_default_message():
    """Test ExecutionResult message defaults to empty string"""
    result = ExecutionResult(
        state=TaskStatus.FAILED,
        exit_code=1
    )
    assert result.message == ""


def test_execution_result_for_timeout():
    """Test ExecutionResult for timeout scenario"""
    result = ExecutionResult(
        state=TaskStatus.TIMEOUT,
        exit_code=-1,
        message="Execution timed out after 7200 seconds"
    )
    assert result.state == TaskStatus.TIMEOUT


def test_execution_result_for_oom():
    """Test ExecutionResult for OOM scenario"""
    result = ExecutionResult(
        state=TaskStatus.OOM,
        exit_code=137,
        message="Process killed by OOM killer (out of memory)"
    )
    assert result.state == TaskStatus.OOM
```

- [ ] **Step 2: 运行测试确保失败**

```bash
cd backend
uv run pytest tests/unit/test_runner_base.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.utils.runner_base'`

- [ ] **Step 3: 实现 ExecutionResult 和 Runner ABC**

```python
# backend/app/utils/runner_base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from app.models import TaskStatus


@dataclass
class ExecutionResult:
    """任务执行结果

    Attributes:
        state: 执行结果状态 (completed, failed, timeout, oom)
        exit_code: 原始进程退出码
        message: 人类可读的结果描述
    """
    state: TaskStatus
    exit_code: int
    message: str = ""


class Runner(ABC):
    """Runner 抽象基类，定义任务执行接口"""

    @abstractmethod
    async def run(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """执行命令并返回结果

        Args:
            command: 要执行的命令及参数
            cwd: 工作目录
            timeout_seconds: 超时时间（秒）
            env: 环境变量

        Returns:
            ExecutionResult: 包含状态、退出码和消息的结果对象
        """
        pass
```

- [ ] **Step 4: 运行测试确保通过**

```bash
cd backend
uv run pytest tests/unit/test_runner_base.py -v
```

Expected: 4 tests PASSED

- [ ] **Step 5: 格式化代码并提交**

```bash
cd backend
uv run black app/utils/runner_base.py tests/unit/test_runner_base.py
git add app/utils/runner_base.py tests/unit/test_runner_base.py
git commit -m "feat: add ExecutionResult dataclass and Runner ABC"
```

---

## Chunk 2: DockerRunner 改造

### Task 2: 更新 DockerRunner 返回 ExecutionResult

**Files:**
- Modify: `backend/app/utils/docker_runner.py`
- Modify: `backend/tests/unit/test_docker_runner.py`

- [ ] **Step 1: 编写 DockerRunner 返回 ExecutionResult 的测试**

```python
# 在 backend/tests/unit/test_docker_runner.py 中更新/添加

import pytest
from unittest.mock import Mock, MagicMock
import asyncio
from app.utils.docker_runner import DockerRunner
from app.utils.runner_base import ExecutionResult
from app.models import TaskStatus


@pytest.fixture
def mock_docker_client():
    """Create a mock Docker client"""
    client = Mock()
    client.containers = Mock()
    return client


@pytest.fixture
def docker_runner(mock_docker_client):
    """Create a DockerRunner with mocked client"""
    runner = DockerRunner(max_memory_gb=4, max_runtime_seconds=3600)
    runner.client = mock_docker_client
    return runner


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_success(docker_runner, mock_docker_client):
    """Test that run() returns ExecutionResult with COMPLETED state on success"""
    # Setup mock container
    mock_container = Mock()
    mock_container.wait.return_value = {"StatusCode": 0}
    mock_container.logs.return_value = b"test output"
    mock_docker_client.containers.run.return_value = mock_container

    result = await docker_runner.run(
        command=["cargo", "build"],
        cwd="/workspace",
        timeout_seconds=3600
    )

    assert isinstance(result, ExecutionResult)
    assert result.state == TaskStatus.COMPLETED
    assert result.exit_code == 0
    assert "success" in result.message.lower()


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_failure(docker_runner, mock_docker_client):
    """Test that run() returns ExecutionResult with FAILED state on error"""
    mock_container = Mock()
    mock_container.wait.return_value = {"StatusCode": 1}
    mock_container.logs.return_value = b"error output"
    mock_docker_client.containers.run.return_value = mock_container

    result = await docker_runner.run(
        command=["cargo", "build"],
        cwd="/workspace",
        timeout_seconds=3600
    )

    assert isinstance(result, ExecutionResult)
    assert result.state == TaskStatus.FAILED
    assert result.exit_code == 1
    assert "exited with code 1" in result.message


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_timeout(docker_runner, mock_docker_client):
    """Test that run() returns ExecutionResult with TIMEOUT state on timeout"""
    import threading

    mock_container = Mock()
    wait_event = threading.Event()

    def blocking_wait():
        wait_event.wait()
        return {"StatusCode": -1}

    mock_container.wait.side_effect = blocking_wait
    mock_docker_client.containers.run.return_value = mock_container

    result = await docker_runner.run(
        command=["cargo", "build"],
        cwd="/workspace",
        timeout_seconds=1  # Very short timeout
    )

    assert isinstance(result, ExecutionResult)
    assert result.state == TaskStatus.TIMEOUT
    assert result.exit_code == -1
    assert "timed out" in result.message.lower()


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_oom(docker_runner, mock_docker_client):
    """Test that run() returns ExecutionResult with OOM state on exit code 137"""
    mock_container = Mock()
    mock_container.wait.return_value = {"StatusCode": 137}
    mock_container.logs.return_value = b"killed"
    mock_docker_client.containers.run.return_value = mock_container

    result = await docker_runner.run(
        command=["cargo", "build"],
        cwd="/workspace",
        timeout_seconds=3600
    )

    assert isinstance(result, ExecutionResult)
    assert result.state == TaskStatus.OOM
    assert result.exit_code == 137
    assert "OOM" in result.message
```

- [ ] **Step 2: 运行测试确保失败**

```bash
cd backend
uv run pytest tests/unit/test_docker_runner.py -v
```

Expected: Tests fail because DockerRunner doesn't return ExecutionResult yet

- [ ] **Step 3: 改造 DockerRunner 返回 ExecutionResult**

```python
# backend/app/utils/docker_runner.py
import asyncio
import os
import docker
from docker.errors import ImageNotFound
from app.utils.runner_base import Runner, ExecutionResult
from app.models import TaskStatus


class DockerRunner(Runner):
    """Docker-based task runner with resource limits"""

    def __init__(self, max_memory_gb: int, max_runtime_seconds: int):
        self.max_memory_gb = max_memory_gb
        self.max_runtime_seconds = max_runtime_seconds
        self.client = docker.from_env()

    def _build_resource_limits(self) -> dict:
        """Build Docker resource limit parameters"""
        memory_bytes = self.max_memory_gb * 1024 * 1024 * 1024
        return {
            "mem_limit": memory_bytes,
            "memswap_limit": memory_bytes,
        }

    def _build_environment(self, env: dict[str, str] | None) -> dict:
        """Build environment variables for container"""
        result = {"CARGO_TERM_COLOR": "always"}
        if env:
            result.update(env)
        return result

    def _determine_result(
        self, exit_code: int, timed_out: bool, timeout_seconds: int
    ) -> ExecutionResult:
        """Determine ExecutionResult based on exit code and timeout status"""
        if timed_out:
            return ExecutionResult(
                state=TaskStatus.TIMEOUT,
                exit_code=-1,
                message=f"Execution timed out after {timeout_seconds} seconds"
            )

        if exit_code == 0:
            return ExecutionResult(
                state=TaskStatus.COMPLETED,
                exit_code=0,
                message="Completed successfully"
            )

        if exit_code == 137:  # SIGKILL - likely OOM
            return ExecutionResult(
                state=TaskStatus.OOM,
                exit_code=137,
                message="Process killed by OOM killer (out of memory)"
            )

        return ExecutionResult(
            state=TaskStatus.FAILED,
            exit_code=exit_code,
            message=f"Process exited with code {exit_code}"
        )

    async def run(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command in Docker container with resource limits"""
        image = "rust:latest"

        # Ensure image exists
        try:
            self.client.images.get(image)
        except ImageNotFound:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self.client.images.pull(image)
            )

        # Prepare environment
        environment = self._build_environment(env)

        # Run container
        container = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: self.client.containers.run(
                image=image,
                command=command,
                working_dir="/workspace",
                volumes={os.path.abspath(cwd): {"bind": "/workspace", "mode": "rw"}},
                environment=environment,
                detach=True,
                **self._build_resource_limits(),
            )
        )

        # Wait for container with timeout
        loop = asyncio.get_event_loop()
        timed_out = False

        def _wait_container() -> dict:
            return container.wait()

        wait_future = loop.run_in_executor(None, _wait_container)
        try:
            wait_result = await asyncio.wait_for(wait_future, timeout=timeout_seconds)
            exit_code = wait_result.get("StatusCode", -1)
        except asyncio.TimeoutError:
            container.stop(timeout=10)
            timed_out = True
            exit_code = -1

        return self._determine_result(exit_code, timed_out, timeout_seconds)
```

- [ ] **Step 4: 运行测试确保通过**

```bash
cd backend
uv run pytest tests/unit/test_docker_runner.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: 格式化代码并提交**

```bash
cd backend
uv run black app/utils/docker_runner.py tests/unit/test_docker_runner.py
git add app/utils/docker_runner.py tests/unit/test_docker_runner.py
git commit -m "refactor: DockerRunner returns ExecutionResult with state and message"
```

---

## Chunk 3: LocalRunner 新建

### Task 3: 创建 LocalRunner 封装本地执行逻辑

**Files:**
- Create: `backend/app/utils/local_runner.py`
- Create: `backend/tests/unit/test_local_runner.py`

- [ ] **Step 1: 编写 LocalRunner 测试**

```python
# backend/tests/unit/test_local_runner.py
import pytest
import asyncio
from unittest.mock import Mock, patch
from app.utils.local_runner import LocalRunner
from app.utils.resource_limit import ResourceLimiter
from app.utils.runner_base import ExecutionResult
from app.models import TaskStatus


@pytest.fixture
def resource_limiter():
    """Create a ResourceLimiter for testing"""
    return ResourceLimiter(
        use_systemd=False,
        max_memory_gb=4,
        max_runtime_seconds=3600
    )


@pytest.fixture
def local_runner(resource_limiter):
    """Create a LocalRunner with mocked dependencies"""
    return LocalRunner(limiter=resource_limiter)


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_success(local_runner):
    """Test that run() returns ExecutionResult with COMPLETED state"""
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        mock_proc.returncode = 0
        mock_proc.communicate = Mock(return_value=asyncio.Future())
        mock_proc.communicate.return_value.set_result((b"output", b""))
        mock_subprocess.return_value = asyncio.Future()
        mock_subprocess.return_value.set_result(mock_proc)

        result = await local_runner.run(
            command=["echo", "hello"],
            cwd="/tmp",
            timeout_seconds=3600
        )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.COMPLETED
        assert result.exit_code == 0
        assert "success" in result.message.lower()


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_failure(local_runner):
    """Test that run() returns ExecutionResult with FAILED state on non-zero exit"""
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        mock_proc.returncode = 1
        mock_proc.communicate = Mock(return_value=asyncio.Future())
        mock_proc.communicate.return_value.set_result((b"", b"error"))
        mock_subprocess.return_value = asyncio.Future()
        mock_subprocess.return_value.set_result(mock_proc)

        result = await local_runner.run(
            command=["false"],
            cwd="/tmp",
            timeout_seconds=3600
        )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.FAILED
        assert result.exit_code == 1


@pytest.mark.asyncio
async def test_run_returns_execution_result_on_timeout(local_runner):
    """Test that run() returns ExecutionResult with TIMEOUT state on timeout"""
    with patch("asyncio.create_subprocess_exec") as mock_subprocess:
        mock_proc = Mock()
        # Simulate timeout by making communicate hang
        mock_proc.communicate = Mock(return_value=asyncio.Future())
        # Don't set result - simulate timeout
        mock_subprocess.return_value = asyncio.Future()
        mock_subprocess.return_value.set_result(mock_proc)

        # Cancel the communicate future to simulate timeout
        async def cancel_after_delay():
            await asyncio.sleep(0.1)
            mock_proc.communicate.return_value.cancel()

        asyncio.create_task(cancel_after_delay())

        result = await local_runner.run(
            command=["sleep", "10"],
            cwd="/tmp",
            timeout_seconds=1
        )

        assert isinstance(result, ExecutionResult)
        assert result.state == TaskStatus.TIMEOUT
        assert "timed out" in result.message.lower()


@pytest.mark.asyncio
async def test_local_runner_implements_runner_interface(local_runner):
    """Test that LocalRunner properly implements Runner ABC"""
    from app.utils.runner_base import Runner
    assert isinstance(local_runner, Runner)
```

- [ ] **Step 2: 运行测试确保失败**

```bash
cd backend
uv run pytest tests/unit/test_local_runner.py -v
```

Expected: `ModuleNotFoundError: No module named 'app.utils.local_runner'`

- [ ] **Step 3: 实现 LocalRunner**

```python
# backend/app/utils/local_runner.py
import asyncio
import signal
from app.utils.runner_base import Runner, ExecutionResult
from app.utils.resource_limit import ResourceLimiter
from app.models import TaskStatus


class LocalRunner(Runner):
    """本地执行 Runner，使用 systemd 或 resource 模块限制资源"""

    def __init__(self, limiter: ResourceLimiter):
        self.limiter = limiter

    def _determine_result(
        self,
        returncode: int | None,
        timed_out: bool,
        timeout_seconds: int
    ) -> ExecutionResult:
        """Determine ExecutionResult based on process result"""
        if timed_out:
            return ExecutionResult(
                state=TaskStatus.TIMEOUT,
                exit_code=-1,
                message=f"Execution timed out after {timeout_seconds} seconds"
            )

        if returncode == 0:
            return ExecutionResult(
                state=TaskStatus.COMPLETED,
                exit_code=0,
                message="Completed successfully"
            )

        # Check for OOM conditions
        # -9 (SIGKILL) could be OOM killer
        if returncode == -signal.SIGKILL or returncode == 137:
            return ExecutionResult(
                state=TaskStatus.OOM,
                exit_code=returncode,
                message="Process killed by OOM killer (out of memory)"
            )

        return ExecutionResult(
            state=TaskStatus.FAILED,
            exit_code=returncode if returncode is not None else -1,
            message=f"Process exited with code {returncode}"
        )

    async def run(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Execute command locally with resource limits"""
        # Build command with resource limits
        wrapped_command = self.limiter.build_command(command, cwd)

        # Prepare environment
        environment = {"CARGO_TERM_COLOR": "always"}
        if env:
            environment.update(env)

        # Determine if we need preexec_fn for resource limits
        preexec_fn = None
        if self.limiter.get_limit_method().value == "resource":
            preexec_fn = self.limiter.apply_resource_limits

        # Run subprocess
        proc = await asyncio.create_subprocess_exec(
            *wrapped_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=environment,
            preexec_fn=preexec_fn,
        )

        # Wait for completion with timeout
        timed_out = False
        try:
            await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout_seconds
            )
        except asyncio.TimeoutError:
            proc.kill()
            timed_out = True

        return self._determine_result(proc.returncode, timed_out, timeout_seconds)
```

- [ ] **Step 4: 运行测试确保通过**

```bash
cd backend
uv run pytest tests/unit/test_local_runner.py -v
```

Expected: All tests PASSED

- [ ] **Step 5: 格式化代码并提交**

```bash
cd backend
uv run black app/utils/local_runner.py tests/unit/test_local_runner.py
git add app/utils/local_runner.py tests/unit/test_local_runner.py
git commit -m "feat: add LocalRunner with ExecutionResult support"
```

---

## Chunk 4: 数据库和模型更新

### Task 4: 更新数据库模型添加 message 字段

**Files:**
- Modify: `backend/app/database.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/schemas.py` (如存在)

- [ ] **Step 1: 更新 TaskRecord 添加 message 字段**

```python
# backend/app/database.py
class TaskRecord(SQLModel, table=True):
    """Task record in database"""
    __tablename__ = "tasks"

    id: int | None = Field(default=None, primary_key=True)
    # ... 其他现有字段 ...

    # 新增字段
    message: str | None = Field(default=None)
```

- [ ] **Step 2: 更新 TaskDetail 模型添加 message 字段**

```python
# backend/app/models.py
class TaskDetail(BaseModel):
    """Task detail response model"""
    # ... 其他现有字段 ...
    message: str | None = None
```

- [ ] **Step 3: 运行测试确保没有破坏现有功能**

```bash
cd backend
uv run pytest tests/unit/test_database.py -v 2>/dev/null || echo "No database tests, checking imports..."
uv run python -c "from app.database import TaskRecord; print('Import OK')"
```

- [ ] **Step 4: 格式化并提交**

```bash
cd backend
uv run black app/database.py app/models.py
git add app/database.py app/models.py
git commit -m "feat: add message field to TaskRecord and TaskDetail"
```

---

## Chunk 5: TaskExecutor 简化

### Task 5: 重构 TaskExecutor 使用新的 Runner 接口

**Files:**
- Modify: `backend/app/services/task_executor.py`

- [ ] **Step 1: 先阅读当前 TaskExecutor 实现**

```bash
cat backend/app/services/task_executor.py
```

- [ ] **Step 2: 改造 TaskExecutor**

```python
# backend/app/services/task_executor.py
import asyncio
import os
import subprocess
from typing import Optional

from app.config import Config
from app.database import Database, TaskRecord
from app.models import TaskStatus
from app.utils.docker_runner import DockerRunner
from app.utils.local_runner import LocalRunner
from app.utils.resource_limit import ResourceLimiter
from app.utils.runner_base import Runner


class TaskExecutor:
    """Handles task execution with resource limits"""

    def __init__(self, config: Config, db: Database):
        self.config = config
        self.db = db
        self._init_runner()

    def _init_runner(self):
        """Initialize the appropriate runner based on configuration"""
        if self.config.use_docker:
            self.runner: Runner = DockerRunner(
                max_memory_gb=self.config.max_memory_gb,
                max_runtime_seconds=self.config.max_runtime_seconds,
            )
        else:
            limiter = ResourceLimiter(
                use_systemd=self.config.use_systemd,
                max_memory_gb=self.config.max_memory_gb,
                max_runtime_seconds=self.config.max_runtime_seconds,
            )
            self.runner: Runner = LocalRunner(limiter=limiter)

    async def execute(self, task: TaskRecord) -> None:
        """Execute a task and update its status"""
        # Update status to running
        task.status = TaskStatus.RUNNING
        self.db.update_task(task)

        try:
            # Prepare command
            command = self._build_command(task)

            # Execute using runner
            result = await self.runner.run(
                command=command,
                cwd=task.workspace_dir,
                timeout_seconds=self.config.max_runtime_seconds,
            )

            # Update task with result
            task.status = result.state
            task.exit_code = result.exit_code
            task.message = result.message
            self.db.update_task(task)

        except Exception as e:
            # Handle unexpected errors
            task.status = TaskStatus.FAILED
            task.exit_code = -1
            task.message = f"Execution error: {str(e)}"
            self.db.update_task(task)

    def _build_command(self, task: TaskRecord) -> list[str]:
        """Build command to execute based on task configuration"""
        # This method extracts the command building logic
        # Actual implementation depends on existing code
        return task.command  # Simplified
```

- [ ] **Step 3: 运行测试确保通过**

```bash
cd backend
uv run pytest tests/unit/test_task_executor.py -v 2>/dev/null || echo "Check imports..."
uv run python -c "from app.services.task_executor import TaskExecutor; print('Import OK')"
```

- [ ] **Step 4: 格式化并提交**

```bash
cd backend
uv run black app/services/task_executor.py
git add app/services/task_executor.py
git commit -m "refactor: TaskExecutor uses new Runner interface with ExecutionResult"
```

---

## Chunk 6: 前端 TaskDetail 展示 message

### Task 6: 在 TaskDetail 页面展示执行消息

**Files:**
- Modify: `frontend/src/views/TaskDetail.vue`

- [ ] **Step 1: 先阅读当前 TaskDetail 实现**

```bash
cat frontend/src/views/TaskDetail.vue
```

- [ ] **Step 2: 更新 TaskDetail 模板添加 message 展示**

```vue
<!-- 在 TaskDetail.vue 的合适位置添加 -->
<template>
  <div class="task-detail">
    <!-- ... 现有内容 ... -->

    <!-- Message 展示卡片 -->
    <div v-if="task.message" class="message-card" :class="messageClass">
      <h4>执行信息</h4>
      <p>{{ task.message }}</p>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  task: Object
})

const messageClass = computed(() => {
  if (!props.task) return ''
  switch (props.task.status) {
    case 'completed':
      return 'message-success'
    case 'failed':
    case 'oom':
      return 'message-error'
    case 'timeout':
      return 'message-warning'
    default:
      return ''
  }
})
</script>

<style scoped>
.message-card {
  margin-top: 16px;
  padding: 12px 16px;
  border-radius: 8px;
  border-left: 4px solid #ccc;
}

.message-card h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #666;
}

.message-card p {
  margin: 0;
  font-size: 14px;
  color: #333;
}

.message-success {
  background: #f0f9eb;
  border-left-color: #67c23a;
}

.message-error {
  background: #fef0f0;
  border-left-color: #f56c6c;
}

.message-warning {
  background: #fdf6ec;
  border-left-color: #e6a23c;
}
</style>
```

- [ ] **Step 3: 检查前端是否有类型定义需要更新**

```bash
grep -r "TaskDetail" frontend/src/ --include="*.ts" --include="*.js" | head -20
```

- [ ] **Step 4: 提交前端更改**

```bash
git add frontend/src/views/TaskDetail.vue
git commit -m "feat: display execution message in TaskDetail"
```

---

## Chunk 7: 集成测试与验证

### Task 7: 运行完整测试套件

**Files:**
- All test files

- [ ] **Step 1: 运行所有后端单元测试**

```bash
cd backend
uv run pytest tests/unit/ -v
```

Expected: All tests PASSED

- [ ] **Step 2: 验证代码格式化**

```bash
cd backend
uv run black app/ tests/ --check
```

Expected: All files formatted

- [ ] **Step 3: 验证前端构建**

```bash
cd frontend
npm run build 2>/dev/null || npm run type-check 2>/dev/null || echo "Skipping frontend build check"
```

- [ ] **Step 4: 最终提交**

```bash
git log --oneline -10
```

---

## 实施检查清单

在完成每个 Task 时，请确认：

- [ ] 遵循 TDD：先写测试，后写实现
- [ ] 使用 `uv run black` 格式化 Python 代码
- [ ] 每个 Task 完成后提交 (git commit)
- [ ] 提交信息遵循 conventional commits 格式
- [ ] 所有单元测试通过

## 可能遇到的问题及解决方案

1. **导入循环**：确保 `runner_base.py` 只导入 `TaskStatus`，不导入其他 app 模块
2. **数据库迁移**：如果已有数据，message 字段需要设为 nullable
3. **前端类型**：如果前端使用 TypeScript，需要更新 Task 类型定义
