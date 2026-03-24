# Runner Execution Result 设计文档

## 背景

当前 `TaskExecutor` 通过 Runner 返回的 `exit_code` 反推任务状态（成功/失败/超时/OOM），这种设计存在以下问题：

1. **逻辑分散**：状态判断逻辑散落在 `TaskExecutor` 的各个地方
2. **语义不明确**：不同 Runner 可能对相同的 exit_code 有不同的解释
3. **难以扩展**：添加新状态需要修改多个文件
4. **缺乏错误信息**：用户无法直观了解任务失败的具体原因

## 设计目标

1. Runner 负责判断执行结果状态，返回结构化的结果对象
2. 保留原始 exit_code 用于调试
3. 提供人类可读的错误消息（msg），在前端展示
4. 统一 Runner 接口，便于未来扩展新的执行方式

## 设计方案

### 1. 核心数据结构与接口

新建 `backend/app/utils/runner_base.py`：

```python
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
    """Runner 抽象基类"""

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

### 2. DockerRunner 改造

修改 `backend/app/utils/docker_runner.py`：

```python
from app.utils.runner_base import Runner, ExecutionResult

class DockerRunner(Runner):
    async def run(...) -> ExecutionResult:
        # ... 执行逻辑 ...

        if exit_code == 0:
            return ExecutionResult(
                state=TaskStatus.COMPLETED,
                exit_code=0,
                message="Completed successfully"
            )
        elif timed_out:
            return ExecutionResult(
                state=TaskStatus.TIMEOUT,
                exit_code=-1,
                message=f"Execution timed out after {timeout_seconds} seconds"
            )
        elif exit_code == 137:  # SIGKILL
            return ExecutionResult(
                state=TaskStatus.OOM,
                exit_code=137,
                message="Process killed by OOM killer (out of memory)"
            )
        else:
            return ExecutionResult(
                state=TaskStatus.FAILED,
                exit_code=exit_code,
                message=f"Process exited with code {exit_code}"
            )
```

### 3. LocalRunner 新建

新建 `backend/app/utils/local_runner.py`，封装 systemd/resource 执行模式：

```python
from app.utils.runner_base import Runner, ExecutionResult
from app.utils.resource_limit import ResourceLimiter

class LocalRunner(Runner):
    """本地执行 Runner，使用 systemd 或 resource 模块限制资源"""

    def __init__(self, limiter: ResourceLimiter):
        self.limiter = limiter

    async def run(...) -> ExecutionResult:
        # 将 TaskExecutor._execute_with_limiter 逻辑移到这里
        # 根据 exit_code 和信号判断状态
        pass
```

### 4. TaskExecutor 简化

修改 `backend/app/services/task_executor.py`：

```python
class TaskExecutor:
    async def _execute_task(self, task: TaskRecord):
        # 根据配置选择 Runner
        if self.config.use_docker:
            runner = DockerRunner(self.config)
        else:
            runner = LocalRunner(self.resource_limiter)

        # 执行并获取结果
        result = await runner.run(
            command=command,
            cwd=task.workspace_dir,
            timeout_seconds=self.config.max_runtime_seconds,
        )

        # 更新数据库
        self.db.update_task_status(
            task_id=task.id,
            status=result.state,
            exit_code=result.exit_code,
            message=result.message,
        )
```

### 5. 数据库模型更新

修改 `backend/app/database.py`：

```python
class TaskRecord(SQLModel, table=True):
    # ... 现有字段 ...
    message: str | None = None  # 执行结果消息
```

### 6. API 响应更新

修改 `backend/app/models.py`：

```python
class TaskDetail(BaseModel):
    # ... 现有字段 ...
    message: str | None = None
```

### 7. 前端展示

修改 `frontend/src/views/TaskDetail.vue`：

在任务详情卡片中，当任务状态不是 COMPLETED 时，展示 message：

```vue
<template>
  <div class="task-detail">
    <!-- ... 其他信息 ... -->

    <div v-if="task.message" class="message-card">
      <h4>执行信息</h4>
      <p>{{ task.message }}</p>
    </div>
  </div>
</template>
```

样式建议：
- COMPLETED：绿色成功提示或隐藏
- FAILED：红色错误提示
- TIMEOUT：橙色警告提示
- OOM：红色错误提示

## 状态映射规则

| Runner | 场景 | state | exit_code | message |
|--------|------|-------|-----------|---------|
| Docker/Local | 正常退出 | COMPLETED | 0 | "Completed successfully" |
| Docker/Local | 超时 | TIMEOUT | -1 | "Execution timed out after {n} seconds" |
| Docker | OOM (SIGKILL) | OOM | 137 | "Process killed by OOM killer (out of memory)" |
| Local | OOM (通过资源限制) | OOM | 根据信号 | "Process killed due to memory limit exceeded" |
| Docker/Local | 其他错误 | FAILED | 实际码 | "Process exited with code {n}" |

## 文件变更清单

### 新增文件
- `backend/app/utils/runner_base.py` - Runner ABC 和 ExecutionResult
- `backend/app/utils/local_runner.py` - 本地执行 Runner

### 修改文件
- `backend/app/utils/docker_runner.py` - 继承 Runner，返回 ExecutionResult
- `backend/app/utils/resource_limit.py` - 可能需要调整接口
- `backend/app/services/task_executor.py` - 简化逻辑，使用新 Runner 接口
- `backend/app/database.py` - TaskRecord 新增 message 字段
- `backend/app/models.py` - TaskDetail 新增 message 字段
- `backend/app/schemas.py` - 如有需要同步更新
- `frontend/src/views/TaskDetail.vue` - 展示 message
- `frontend/src/services/api.js` - 如有类型定义需更新

### 数据库迁移
- 添加 `message` 字段到 task 表

## 测试策略

1. **单元测试**：更新 `test_docker_runner.py`，验证返回的 ExecutionResult
2. **单元测试**：新增 `test_local_runner.py`
3. **集成测试**：验证 TaskExecutor 正确传递 message 到数据库

## 兼容性考虑

1. **数据库**：message 字段可为 null，兼容历史数据
2. **API**：message 字段可选，前端兼容旧数据
3. **配置**：无需配置变更
