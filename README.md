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

## Quick Start

1. Copy config:
   ```bash
   cp config.toml.example config.toml
   ```

2. Start all services with Docker Compose:
   ```bash
   export RUNNER_TOKEN="your-admin-created-token"
   docker compose up --build
   ```

3. Or start individually:
   - Backend: `cd backend && uv sync && uv run python -m app.main`
   - Runner: `cd backend && RUNNER_SERVER_URL=http://localhost:8080 RUNNER_ID=local RUNNER_TOKEN=... uv run python -m runner`
   - Frontend: `cd frontend && npm install && npm run dev`


## 分布式 Runner 部署（简版）

1. 在 `config.toml` 配置 `lease_ttl_seconds`。
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
uv run python -m runner
```

5. Runner 启动后会周期性心跳并 claim 任务；执行期间通过 Runner API 上报事件与日志分片。

### Runner 管理语义

- `DELETE /api/admin/runners/{runner_id}` 为物理删除（永久移除 runner 记录）。
- `POST /api/admin/runners/{runner_id}/disable` 为禁用（`enabled=false`），runner 仍保留在列表中。
- `POST /api/admin/runners/{runner_id}/enable` 为启用（`enabled=true`）。
- 对于已被删除或禁用的 runner，其心跳/claim/事件/日志上报会立刻鉴权失败（403）。
- 若该 runner 已 claim 任务，任务会先保持 `running`，并在租约到期后由调度器自动回收并重新排队（`pending`）。
