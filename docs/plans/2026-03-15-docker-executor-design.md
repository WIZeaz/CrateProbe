# Docker 执行器设计文档

## 背景

当前后端使用 `systemd-run`（优先）或 `resource` 模块来限制任务资源。本设计新增 Docker 作为第三种执行模式，提供更完善的资源隔离和环境一致性。

## 目标

1. 添加 Docker 执行模式作为可选方案
2. 支持自定义 Docker 镜像
3. 保持与现有系统（日志、状态、统计）兼容
4. 提供更精确的资源限制（内存、CPU、运行时间）

## 架构设计

### 配置变更

```toml
[execution]
# 执行模式：systemd, resource, docker
execution_mode = "docker"
max_jobs = 8
max_memory_gb = 20
max_runtime_hours = 10
max_cpus = 4  # 新增：Docker模式下限制CPU核心数

# Docker 配置（当 execution_mode = "docker" 时生效）
[execution.docker]
image = "rust-cargo-rapx:latest"
pull_policy = "if-not-present"  # 选项: always, if-not-present, never
```

### 新增组件

#### 1. DockerRunner 类

**位置**: `backend/app/utils/docker_runner.py`

**职责**:
- 管理 Docker 容器生命周期
- 应用资源限制
- 处理容器日志输出
- 返回执行结果和退出码

**核心方法**:

```python
class DockerRunner:
    def __init__(self, image: str, max_memory_gb: int, max_runtime_hours: int, max_cpus: int):
        ...

    async def run(
        self,
        command: List[str],
        workspace_dir: Path,
        stdout_log: Path,
        stderr_log: Path
    ) -> int:
        """
        在 Docker 容器中执行命令
        返回容器退出码
        """
        ...

    def ensure_image(self, pull_policy: str) -> bool:
        """确保镜像存在，如需要则拉取"""
        ...
```

#### 2. Dockerfile.executor

**位置**: `backend/docker/Dockerfile.executor`

基础镜像包含：
- Rust 工具链
- cargo-rapx 工具
- 必要的系统依赖

```dockerfile
FROM rust:latest

# 安装 cargo-rapx
RUN cargo install cargo-rapx

# 创建工作目录
WORKDIR /workspace

# 默认命令
CMD ["cargo", "rapx", "--help"]
```

#### 3. 配置类更新

**位置**: `backend/app/config.py`

新增字段：
- `execution_mode`: str ("systemd" | "resource" | "docker")
- `docker_image`: str
- `docker_pull_policy`: str
- `max_cpus`: int

### 执行流程

```
┌─────────────────┐
│   execute_task  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ prepare_workspace│  下载并解压 crate
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ 检查 execution_mode
└────────┬────────┘
         │
    ┌────┴────┬────────────┐
    ▼         ▼            ▼
 systemd   resource      docker
    │         │            │
    ▼         ▼            ▼
┌──────┐  ┌──────┐   ┌─────────────────┐
│systemd│  │resource│  │ DockerRunner.run │
│ -run  │  │limits │   │  - 挂载卷        │
└──────┘  └──────┘   │  - 应用资源限制   │
                     │  - 执行命令       │
                     └─────────────────┘
                              │
                              ▼
                     ┌─────────────────┐
                     │  等待容器完成    │
                     │  收集退出码      │
                     └─────────────────┘
                              │
                              ▼
                     ┌─────────────────┐
                     │  统计生成项      │
                     │  更新任务状态    │
                     └─────────────────┘
```

### Docker 资源限制映射

| 配置项 | Docker 参数 | 说明 |
|--------|-------------|------|
| `max_memory_gb` | `--memory={value}g` | 内存限制 |
| `max_memory_gb` | `--memory-swap={value}g` | 禁用 swap |
| `max_cpus` | `--cpus={value}` | CPU 核心数限制 |
| `max_runtime_hours` | `--stop-timeout={value*3600}` 或应用层超时 | 运行时间限制 |

### 日志处理

Docker 模式下保持与现有方式一致：

1. **方案一（Volume Mount）**:
   - 挂载主机日志文件到容器
   - 容器内命令直接写入

2. **方案二（Docker SDK）**:
   - 使用 Docker SDK 读取容器日志流
   - 应用层写入日志文件

推荐**方案二**，更灵活且易于错误处理。

### 错误处理

| 场景 | 处理方式 |
|------|----------|
| 镜像不存在 | 根据 pull_policy 拉取或报错 |
| 容器启动失败 | 捕获异常，标记任务 FAILED |
| 内存超限 (OOM) | Docker 退出码 137，映射为 oom 状态 |
| 运行超时 | 强制停止容器，标记为 timeout |
| 网络问题 | 重试或标记 FAILED |

### 兼容性考虑

1. **向后兼容**: 默认 `execution_mode = "systemd"`，现有配置无需修改
2. **混合模式**: 不同任务可使用不同模式（未来扩展）
3. **回退机制**: Docker 失败时可选回退到 resource 模式（可选功能）

## 测试策略

### 单元测试

**位置**: `backend/tests/unit/test_docker_runner.py`

测试内容：
- 镜像存在性检查
- 命令构建正确性
- 资源限制参数转换
- 日志流处理

### 集成测试

**位置**: `backend/tests/integration/test_docker_execution.py`

测试内容：
- 完整任务执行流程
- 资源限制实际效果
- 错误场景处理

## 实施步骤

1. **更新配置** (`config.py`)
   - 添加 execution_mode 等字段

2. **创建 DockerRunner** (`docker_runner.py`)
   - 实现核心容器管理功能

3. **修改 TaskExecutor** (`task_executor.py`)
   - 根据 execution_mode 选择执行方式

4. **添加 Dockerfile** (`docker/Dockerfile.executor`)
   - 提供基础执行镜像

5. **更新 config.toml.example**
   - 添加 Docker 配置示例

6. **编写测试**
   - 单元测试和集成测试

## 风险评估

| 风险 | 缓解措施 |
|------|----------|
| Docker 未安装 | 启动时检查，提示用户 |
| 镜像拉取失败 | 清晰的错误信息，支持离线镜像 |
| 性能开销 | Volume 挂载避免文件拷贝 |
| 权限问题 | 文档说明 docker 组权限 |
