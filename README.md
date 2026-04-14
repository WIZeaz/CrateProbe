# CrateProbe

CrateProbe 是一个自动化 Rust crate 分析引擎，可下载 crate、运行实验，并通过 Web 界面实时展示进度和结果。

## 功能特性

- 自动下载和测试Rust crates
- 并发任务执行（可配置）
- 实时Web界面监控
- 资源限制（内存、时间）
- 详细的日志记录和展示

## 技术栈

- **后端**: Python 3.10+, FastAPI, SQLite
- **前端**: Vue 3, Tailwind CSS, Vite

## 文档

详细设计文档请见：[docs/plans/2026-01-26-experiment-platform-design.md](docs/plans/2026-01-26-experiment-platform-design.md)

## 快速开始

### 1. 配置

项目使用**统一的配置文件** `config.toml` 管理前后端配置。

```bash
# 复制示例配置文件
cp config.toml.example config.toml

# 根据需要编辑配置（可选）
# 默认配置：后端端口 8080，前端端口 5173
vim config.toml
```

**配置文件说明**：
- `[server]` - 后端 FastAPI 服务器配置（端口、地址）
- `[workspace]` - 工作空间目录
- `[execution]` - 任务执行配置（并发数、资源限制）
- `[database]` - 数据库配置
- `[logging]` - 日志配置
- `[frontend]` - 前端开发服务器配置和代理设置

### 2. 启动后端

本项目使用 [uv](https://github.com/astral-sh/uv) 进行Python依赖管理。

```bash
cd backend

# 安装依赖
uv sync

# 运行测试（可选）
uv run pytest

# 启动后端服务
uv run python -m app.main
```

后端将在配置文件指定的端口启动（默认 `http://localhost:8080`）

### 3. 启动前端

```bash
cd frontend

# 安装依赖
npm install

# 启动开发服务器
npm run dev
```

前端将自动：
- 从 `config.toml` 读取配置
- 在配置的端口启动（默认 `http://localhost:5173`）
- 代理 `/api` 和 `/ws` 请求到后端

### 4. 访问应用

打开浏览器访问 `http://localhost:5173`

## 开发

本项目遵循TDD原则，请在实现功能前编写测试。

详细实施计划请见 docs/plans/ 目录。

## 分布式 Runner 部署（简版）

1. 在 `config.toml` 设置：`[distributed].enabled = true`，并配置 `lease_ttl_seconds`。
2. 设置 `security.admin_token`（用于 Runner 管理 API 的 `X-Admin-Token` 鉴权）。
3. 通过管理 API 创建 Runner（返回一次性明文 token）：

```bash
curl -X POST "http://localhost:8080/api/admin/runners" \
  -H "X-Admin-Token: <admin_token>" \
  -H "Content-Type: application/json" \
  -d '{"runner_id":"runner-01"}'
```

4. 在 Runner 机器配置环境变量并启动进程：

```bash
export RUNNER_SERVER_URL="http://<server-host>:8080"
export RUNNER_ID="runner-01"
export RUNNER_TOKEN="<token-from-create-runner-response>"

cd backend
uv run python -m app.runner
```

5. Runner 启动后会周期性心跳并 claim 任务；执行期间通过 Runner API 上报事件与日志分片。

### 删除 Runner 的语义

- `DELETE /api/admin/runners/{runner_id}` 为软删除（`enabled=false`），不是物理删除。
- 被删除后，该 Runner 的心跳/claim/事件/日志上报会立刻鉴权失败（403）。
- 若该 Runner 已 claim 任务，任务会先保持 `running`，并在租约到期后由调度器自动回收并重新排队（`pending`）。
