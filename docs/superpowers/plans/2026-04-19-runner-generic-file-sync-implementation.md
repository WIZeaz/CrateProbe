# Runner/Backend 通用文件同步接口 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 runner→backend 文件上传从 `log_type` 绑定改为 `file_name` 通用协议，新增全量覆盖接口，统一落盘到 `workspace/logs/{task_id}-{file_name}`，并将 `stats.yaml` 迁移到全量上传。

**Architecture:** backend 提供两个通用文件接口（增量/全量），两者都依赖 lease + 鉴权并通过 `file_name` 决定逻辑目标；backend 统一将所有文件落盘到 `workspace/logs` 且使用 `{task_id}-` 前缀。runner 侧保留现有本地文件生成逻辑，仅上传协议改为 `file_name`，其中 `stats.yaml` 走全量覆盖，其它文件保持增量 offset/chunk_seq。

**Tech Stack:** Python 3.11+, FastAPI, SQLite, pytest, httpx, asyncio

---

## 文件变更映射

| 文件 | 变更 | 责任 |
|---|---|---|
| `backend/app/main.py` | 修改 | 新增通用文件增量/全量接口；移除旧 `log_type` chunk 路径；统一日志读取映射到 `{task_id}-{file_name}` |
| `backend/app/database.py` | 修改 | `record_task_log_chunk` 语义升级为 `file_name`；新增按 task 清理 chunk 记录方法（如未存在） |
| `backend/runner/client.py` | 修改 | 新增 `send_file_chunk` 与 `upload_file_full`，对接新接口 |
| `backend/runner/reporter.py` | 修改 | 拆分 `incremental_files` / `full_files`；`stats.yaml` 走全量上报；上传 key 改为 `file_name` |
| `backend/runner/executor.py` | 修改 | `TaskReporter` 初始化改用文件名 key；`stats.yaml` 归入全量列表 |
| `backend/runner/config.py` | 修改 | 新增 `file_sync_interval_seconds` + `RUNNER_FILE_SYNC_INTERVAL_SECONDS`，移除 `log_flush_interval_seconds` |
| `backend/tests/unit/test_main.py` | 修改 | 覆盖新文件接口（200/400/403/409）与读取映射行为 |
| `backend/tests/unit/test_database.py` | 修改 | 覆盖 `(task_id,file_name,chunk_seq)` 幂等与 task 级 chunk 清理 |
| `backend/tests/unit/runner/test_runner_client.py` | 修改 | 覆盖新 client API 路径与重试行为 |
| `backend/tests/unit/runner/test_reporter.py` | 修改 | 覆盖增量/全量混合上报与 `stats.yaml` 全量迁移 |
| `backend/tests/unit/runner/test_executor.py` | 修改 | 适配 reporter 新参数和文件名 key |
| `backend/tests/unit/test_config.py` | 修改 | 覆盖新环境变量默认值/读取/非法值；验证旧变量不再生效 |
| `backend/tests/integration/test_api.py` | 修改 | 覆盖文件上传端到端（增量+全量）及读取映射到 logs 目录 |
| `backend/tests/integration/test_runner_control_api.py` | 修改 | 适配 runner 控制面从 `log_type` 上传迁移到 `file_name` 上传 |
| `RUNNER.md`（如含 env 示例） | 修改 | 更新 runner 环境变量说明与启动示例 |

---

## Chunk 1: Backend 通用文件接口与路径统一

### Task 1: 用测试锁定新 API 合同（先失败）

**Files:**
- Modify: `backend/tests/unit/test_main.py`

- [ ] **Step 1: 为增量接口添加成功用例（200 + appended=true）**

在 `backend/tests/unit/test_main.py` 增加测试，调用：

`POST /api/runners/{runner_id}/tasks/{task_id}/files/chunks`

断言：
- `status_code == 200`
- `json()["appended"] is True`
- 文件写入 `workspace/logs/{task_id}-stdout.log`

- [ ] **Step 2: 为增量接口添加 stale/duplicate 用例（200 + appended=false）**

同一 `file_name` 连续提交 `chunk_seq=1` 两次，断言第二次：
- `status_code == 200`
- `json()["appended"] is False`
- `json()["reason"] == "stale_or_duplicate"`

- [ ] **Step 3: 为全量接口添加覆盖语义用例**

调用：
- 第一次 `PUT /api/runners/{runner_id}/tasks/{task_id}/files`，`content="v1"`
- 第二次同接口，`content="v2"`

断言最终 `workspace/logs/{task_id}-stats.yaml` 内容为 `v2`。

- [ ] **Step 4: 为 `file_name` 非法输入添加 400 用例**

覆盖 `""`, `"../x"`, `"a/b"`, `"a\\b"`。

- [ ] **Step 5: 为 lease/auth 失败添加 403/409 用例**

分别覆盖：
- 无 Authorization（403）
- 错 token（403）
- lease 不匹配（409）

- [ ] **Step 6: 先运行新测试并确认失败**

Run: `cd backend && uv run pytest tests/unit/test_main.py -k "file_chunk or file_full or stale_or_duplicate or file_name" -v`

Expected: FAIL（新接口尚未实现）

### Task 2: 实现 backend 通用文件接口与解析

**Files:**
- Modify: `backend/app/main.py`

- [ ] **Step 1: 添加新请求模型**

新增：
- `RunnerTaskFileChunkRequest(lease_token, file_name, chunk_seq, content)`
- `RunnerTaskFileFullRequest(lease_token, file_name, content)`

- [ ] **Step 2: 添加 `file_name` 校验 helper（最小实现）**

增加 helper：

```python
def _validate_file_name(file_name: str) -> str:
    if not file_name or "/" in file_name or "\\" in file_name or ".." in file_name:
        raise HTTPException(status_code=400, detail="Invalid file_name")
    return file_name
```

- [ ] **Step 3: 添加统一落盘 helper**

```python
def _resolve_task_file_path(task_id: int, file_name: str, cfg: Config) -> Path:
    return cfg.workspace_path / "logs" / f"{task_id}-{file_name}"
```

- [ ] **Step 4: 实现增量接口**

新增路由：
- `POST /api/runners/{runner_id}/tasks/{task_id}/files/chunks`

流程：
1. runner 鉴权 + lease 校验
2. 校验 `file_name`、`chunk_seq >= 1`
3. 调用 `db.record_task_file_chunk(task_id, file_name, chunk_seq)`
4. 若可写则 append；否则返回 `{"appended": False, "reason": "stale_or_duplicate"}`

- [ ] **Step 5: 实现全量接口**

新增路由：
- `PUT /api/runners/{runner_id}/tasks/{task_id}/files`

流程：
1. runner 鉴权 + lease 校验
2. 校验 `file_name`
3. 直接覆盖写入
4. 返回 `{"updated": True, "bytes": len(content.encode("utf-8"))}`

- [ ] **Step 6: 显式移除旧 `log_type` 上传接口与相关常量**

从 `backend/app/main.py` 删除：
- `POST /api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks`
- `RUNNER_CHUNK_LOG_TYPES`
- 与旧 ingest 路径绑定的分支逻辑

确保 runner 侧 ingest 仅保留新通用文件接口。

- [ ] **Step 7: 更新日志读取映射到新路径**

将 `/api/tasks/{task_id}/logs/{log_name}` 与 `/raw` 改为固定映射：
- `stdout -> stdout.log`
- `stderr -> stderr.log`
- `runner -> runner.log`
- `miri_report -> miri_report.txt`
- `stats-yaml -> stats.yaml`

读取路径统一使用 `_resolve_task_file_path(task_id, mapped_file_name, config)`。

- [ ] **Step 8: 更新 `_clear_task_logs`**

改为删除 `workspace/logs/{task_id}-*`，并清理该 task 的 chunk 记录。

- [ ] **Step 9: 运行 backend 相关测试**

Run: `cd backend && uv run pytest tests/unit/test_main.py tests/integration/test_api.py -v`

Expected: PASS

- [ ] **Step 10: Commit**

Run:

```bash
cd backend && git add app/main.py tests/unit/test_main.py tests/integration/test_api.py
cd backend && git commit -m "feat(api): add generic incremental/full file upload endpoints"
```

### Task 3: 调整数据库幂等 API（file_name 语义）

**Files:**
- Modify: `backend/app/database.py`
- Modify: `backend/tests/unit/test_database.py`

- [ ] **Step 1: 先写失败测试（file_name 维度幂等）**

新增测试要点：
- 同 task + 同 file_name + 重复 chunk_seq => False
- 同 task + 不同 file_name + 相同 chunk_seq => True（相互独立）
- task reset/clear 后 chunk_seq 可从 1 重新开始

- [ ] **Step 2: 将方法名升级为 `record_task_file_chunk`**

实现策略：保持 DB 表结构不变，参数语义从 `log_type` 改为 `file_name`。

- [ ] **Step 3: 增加 task 级 chunk 记录清理方法**

如不存在则新增：

```python
def clear_task_file_chunk_sequences(self, task_id: int) -> None:
    ...
```

- [ ] **Step 4: 运行数据库测试**

Run: `cd backend && uv run pytest tests/unit/test_database.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

Run:

```bash
cd backend && git add app/database.py tests/unit/test_database.py
cd backend && git commit -m "refactor(db): treat chunk idempotency key as file_name"
```

---

## Chunk 2: Runner 协议迁移（stats.yaml 全量）+ 配置迁移

### Task 4: RunnerControlClient 支持新接口

**Files:**
- Modify: `backend/runner/client.py`
- Modify: `backend/tests/unit/runner/test_runner_client.py`

- [ ] **Step 1: 写失败测试（新 path 与方法）**

新增测试断言：
- `send_file_chunk(task_id, payload)` 访问 `/files/chunks`
- `upload_file_full(task_id, payload)` 访问 `/files`（PUT）
- 5xx 重试策略与现有 `send_event` 一致

- [ ] **Step 2: 实现 client 方法**

新增：
- `async def send_file_chunk(self, task_id: int, payload: dict[str, Any])`
- `async def upload_file_full(self, task_id: int, payload: dict[str, Any])`

必要时新增 `_put_with_retry`（与 `_post_with_retry` 保持相同行为）。

- [ ] **Step 3: 移除旧 `send_log_chunk(task_id, log_type, payload)` API**

将调用方全部改为 `send_file_chunk` / `upload_file_full`，避免并存两套上传协议。

- [ ] **Step 4: 运行 client 测试**

Run: `cd backend && uv run pytest tests/unit/runner/test_runner_client.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add runner/client.py tests/unit/runner/test_runner_client.py
cd backend && git commit -m "feat(runner): add generic file sync client methods"
```

### Task 5: TaskReporter 拆分增量/全量同步

**Files:**
- Modify: `backend/runner/reporter.py`
- Modify: `backend/runner/executor.py`
- Modify: `backend/tests/unit/runner/test_reporter.py`
- Modify: `backend/tests/unit/runner/test_executor.py`

- [ ] **Step 1: 写失败测试（`stats.yaml` 走全量，其它走增量）**

最少覆盖：
- `stdout.log` 仍调用 chunk 上传，`chunk_seq` 递增
- `stats.yaml` 调用 full 上传
- `stats.yaml` 内容未变化时不重复 full 上传

- [ ] **Step 2: 在 reporter 中引入两类文件集合**

构造参数建议：
- `incremental_file_paths: dict[str, Path]`
- `full_file_paths: dict[str, Path]`

内部状态：
- 增量：`_sent_offsets`, `_next_chunk_seq`
- 全量：`_last_full_content_hash`（或 `_last_full_content`）

- [ ] **Step 3: 实现全量同步循环**

在 `run()` 每个周期执行：
1. `_flush_incremental_files()`
2. `_flush_full_files()`
3. `_maybe_send_progress()`

- [ ] **Step 4: 修改 executor 初始化**

将现有 `log_paths` 语义改为文件名：
- 增量：`stdout.log`, `stderr.log`, `runner.log`, `miri_report.txt`
- 全量：`stats.yaml`

- [ ] **Step 5: 运行 runner 单元测试**

Run: `cd backend && uv run pytest tests/unit/runner/test_reporter.py tests/unit/runner/test_executor.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add runner/reporter.py runner/executor.py tests/unit/runner/test_reporter.py tests/unit/runner/test_executor.py
cd backend && git commit -m "feat(runner): migrate stats.yaml to full upload and keep others incremental"
```

### Task 6: Runner 配置环境变量迁移

**Files:**
- Modify: `backend/runner/config.py`
- Modify: `backend/tests/unit/test_config.py`
- Modify: `RUNNER.md`

- [ ] **Step 1: 写失败测试（新变量 + 旧变量废弃）**

新增断言：
- 默认 `file_sync_interval_seconds == 30.0`
- 设置 `RUNNER_FILE_SYNC_INTERVAL_SECONDS=5` 生效
- `RUNNER_LOG_FLUSH_INTERVAL_SECONDS` 不再被读取

- [ ] **Step 2: 实现配置字段迁移**

在 `RunnerConfig` 中：
- 删除 `log_flush_interval_seconds`
- 新增 `file_sync_interval_seconds`
- `from_env()` 读取 `RUNNER_FILE_SYNC_INTERVAL_SECONDS`

- [ ] **Step 3: 调整调用方**

`TaskReporter` 初始化参数改为 `file_sync_interval`，所有引用同步替换。

- [ ] **Step 4: 更新 RUNNER 文档示例**

把示例中的旧变量替换为新变量。

- [ ] **Step 5: 运行配置测试**

Run: `cd backend && uv run pytest tests/unit/test_config.py -v`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd backend && git add runner/config.py tests/unit/test_config.py RUNNER.md runner/reporter.py runner/executor.py
cd backend && git commit -m "refactor(config): replace log flush env with file sync interval"
```

---

## Chunk 3: 集成验证与收尾

### Task 7: 更新 integration 测试覆盖新上传链路

**Files:**
- Modify: `backend/tests/integration/test_api.py`
- Modify: `backend/tests/integration/test_runner_control_api.py`

- [ ] **Step 1: 添加 runner 增量上传到 logs 目录测试**

覆盖：`file_name=stdout.log`，多次 chunk 后读取 `/api/tasks/{id}/logs/stdout` 能看到拼接结果。

- [ ] **Step 2: 添加 runner 全量上传覆盖测试**

覆盖：`file_name=stats.yaml` 多次 `PUT` 后，`/api/tasks/{id}/logs/stats-yaml/raw` 返回最终覆盖内容。

- [ ] **Step 3: 添加安全边界测试**

非法 `file_name` 返回 400；lease 不匹配返回 409。

- [ ] **Step 4: 运行 integration 测试**

Run: `cd backend && uv run pytest tests/integration/test_api.py -v`

Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add tests/integration/test_api.py
cd backend && git commit -m "test(integration): cover generic file chunk/full upload flow"
```

### Task 8: 全量回归与最终校验

**Files:**
- No code changes (verification only)

- [ ] **Step 1: 运行关键单测集合**

Run:

```bash
cd backend && uv run pytest tests/unit/test_main.py tests/unit/test_database.py tests/unit/test_config.py tests/unit/runner/test_runner_client.py tests/unit/runner/test_reporter.py tests/unit/runner/test_executor.py -v
```

Expected: PASS

- [ ] **Step 2: 运行 integration 关键集合**

Run:

```bash
cd backend && uv run pytest tests/integration/test_api.py tests/integration/test_runner_control_api.py -v
```

Expected: PASS

- [ ] **Step 3: 快速手工验证（可选）**

1. 启动 backend + runner
2. 创建任务并观察 `workspace/logs/{task_id}-*`
3. 确认 `stats.yaml` 文件被覆盖更新而不是追加

- [ ] **Step 4: 最终提交（若本任务以单 commit 收敛）**

```bash
git status
git add -A
git commit -m "feat: migrate runner/backend file sync to generic chunk/full APIs"
```

---

## 实施注意事项

- 严格保持 YAGNI：不引入 backend allowlist，不增加额外元数据协议。
- 保持日志读取 API 的兼容键（`stdout/stderr/runner/miri_report/stats-yaml`）不变。
- 先测试后实现（TDD）：每个任务先写失败测试，再写最小实现，再回归。
- 每个任务完成即 commit，避免大批量难回滚改动。
