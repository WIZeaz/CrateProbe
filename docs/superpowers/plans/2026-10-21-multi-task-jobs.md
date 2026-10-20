# 多 Task 支持重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把平台从"固定 cargo rapx 任务"重构为"用户上传 task 定义（zip + task.toml）+ 按 job 执行"的多任务实验平台。

**Architecture:** 共享 `core/task_spec.py` 解析模块；backend 侧 `tasks`（定义）与 `jobs`（执行记录，由旧 `tasks` 表演化）双表，runner 通过统一 files 接口推送日志与 watch 文件；runner 侧按 task.toml 驱动 per-job DockerRunner。backend 先行（旧 runner 暂时失联可接受，同分支一起交付），然后 runner，最后前端。

**Tech Stack:** Python 3.10+ / FastAPI 0.104 / pydantic v2 / SQLite（sqlite3 直用）/ docker SDK / httpx；前端 Vue 3 + Vite + axios。

**Spec:** `docs/superpowers/specs/2026-10-21-multi-task-jobs-design.md`

## Global Constraints

- 所有 Python 代码提交前必须 Black 格式化：`cd backend && uv run black app/ tests/ runner/ core/`。
- 测试命令：`cd backend && uv run pytest`；前端验证：`cd frontend && npm run build`。
- pydantic v2 默认忽略未知字段——runner 在 Chunk D 完成前仍发送旧字段是安全的。
- 不引入 `schema_version` 或任何向前兼容机制；不保留 cases/pocs/compile_failed 任何痕迹。
- 路径安全：所有来自外部的相对路径（zip 条目、files/artifacts 的 path）必须拒绝绝对路径与 `..`。
- task.toml 校验规则以 spec 文档为准：必填 `name`/`image`/`cmd`/`workspace`（非空字符串，workspace 为绝对路径），`artifacts`/`watch` 为相对路径列表，`limit.cpu` 正数、`limit.mem` docker 格式字符串、`limit.timeout` 时长字符串或整数秒，`env` 为字符串表，未知字段忽略。
- 容量上限（backend config 可配）：full 推送 10MB（截断尾部）、artifacts 单文件 1GB、task zip 上传 100MB。
- commit message 遵循仓库现有 conventional commits 风格。

## Review Focus

1. **路径穿越**：zip 上传条目、runner 解压 zip、files/artifacts 推送的 `path` 三处都必须挡住 `../evil` 与 `/abs`——Tasks 1/4/5/9 各有测试。
2. **task zip 拉取失败**（404 或 sha256 不符）：job 必须以 failed 终态收场且带明确 message，不能卡住——Task 12 测试。
3. **迁移幂等性**：旧库迁移后再启动不得重复建 dummy task 或丢数据——Task 2 测试。
4. **append 模式乱序/重复 chunk**：重复 `chunk_seq` 不得追加——Task 4/6 测试。
5. **retry/claim 残留**：retry 或重新 claim 后，旧的 files/artifacts 必须清空——Task 3/6 测试。

---

## Chunk A: 共享 task spec 模块

### Task 1: `core/task_spec.py` 解析与校验

**Files:**
- Create: `backend/core/task_spec.py`
- Test: `backend/tests/unit/core/test_task_spec.py`（新建目录含 `__init__.py`）

**Interfaces:**
- Produces（后续所有任务依赖）:
  - `TaskSpecError(ValueError)`
  - `@dataclass TaskLimits`: `cpu: Optional[float]`, `mem: Optional[str]`, `timeout_seconds: Optional[int]`
  - `@dataclass TaskSpec`: `name: str`, `image: str`, `cmd: str`, `workspace: str`, `desc: Optional[str]`, `artifacts: list[str]`, `watch: list[str]`, `limits: TaskLimits`, `env: dict[str, str]`
  - `parse_task_spec(data: bytes) -> TaskSpec` — TOML 解析 + 全部校验，失败抛 `TaskSpecError`
  - `spec_to_dict(spec: TaskSpec) -> dict` / `spec_from_dict(d: dict) -> TaskSpec` — DB JSON 往返（`from_dict` 信任输入不重复校验）
  - `sha256_hex(data: bytes) -> str`

- [ ] **Step 1: 写失败测试**

覆盖：完整 toml 解析出全部字段；仅必填字段时可选字段为默认（`desc=None`、列表空、`limits` 全 None、`env={}`）；缺 `name`/`cmd` 为空/相对 `workspace`/`watch` 含 `..` 或绝对路径/`cpu=-1`/`mem="abc"`/`timeout="10x"`/`env` 值非字符串 → 各抛 `TaskSpecError`；`timeout` 的 `"2h"→7200`、`"30m"→1800`、`"45s"→45`、`90→90`；`mem` 接受 `"4g"`/`"512m"`/`"4096"`；未知字段忽略；`spec_to_dict`/`spec_from_dict` 往返相等；`sha256_hex(b"")` 为已知向量 `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/unit/core/test_task_spec.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 实现 `core/task_spec.py`**

TOML 解析用 `tomllib`（py<3.11 回退 `tomli`，参照 `app/config.py` 的导入方式）。mem 校验正则 `^\d+(\.\d+)?[bkmgBKMG]?$`；timeout 正则 `^\d+(\.\d+)?[smh]$` 或正整数；路径条目用 `PurePosixPath` 判绝对、拒含 `..` 分段。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/core/test_task_spec.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black core/ tests/unit/core/
git add backend/core/task_spec.py backend/tests/unit/core/
git commit -m "feat(core): add task.toml spec parsing and validation"
```

## Chunk B: Backend 数据层

### Task 2: 数据库 schema 迁移与 Database 层重构

**Files:**
- Modify: `backend/app/database.py`（整体重写式修改）
- Test: `backend/tests/unit/test_database.py`（更新）+ `backend/tests/unit/test_migration.py`（新建）

**Interfaces:**
- Produces:
  - `@dataclass TaskDefRecord`: `id, name, desc, sha256, zip_path, spec(str, JSON), created_at, updated_at(Optional)`
  - `@dataclass JobRecord`：旧 `TaskRecord` 去掉 `case_count/poc_count/compile_failed/workspace_path/stdout_log/stderr_log`，新增 `task_id: int, task_name: str, task_sha256: str, task_spec: str`
  - task 定义方法：`create_task_definition(name, desc, sha256, zip_path, spec) -> int`、`get_task_definition(task_id)`、`get_task_definition_by_sha256(sha)`、`list_task_definitions()`、`update_task_definition(task_id, *, name, desc, sha256, zip_path, spec) -> bool`、`delete_task_definition(task_id) -> Optional[TaskDefRecord]`
  - job 方法（旧方法改名+裁剪）：`create_job(crate_name, version, task_id, task_name, task_sha256, task_spec) -> int`、`get_job`、`get_all_jobs`、`get_jobs_by_status`、`get_pending_jobs_ordered`、`delete_job`、`update_job_status`、`update_job_priority`、`update_job_pid`、`reset_job_for_retry`、`update_job_state_sync`、`reconcile_stale_jobs(max_sync_interval_seconds)`（改名自 `reconcile_stale_tasks`）、`extend_runner_job_leases`、`record_job_file_chunk(job_id, path, chunk_seq) -> bool`、`reset_job_file_chunk_sequences(job_id)`
  - `apply_job_sync(job_id, sync_seq, status, exit_code=None, message=None) -> Optional[bool]`（删除三个统计参数）
  - `claim_pending_job(runner_id, lease_ttl_seconds) -> Optional[JobRecord]`：领取时 LEFT JOIN `tasks`，若 task 行存在且 sha256 与 job 快照不同则刷新快照三列；task 行不存在则按旧快照照常发放
  - `Database.init_db(workspace: Optional[Path] = None)`：新 schema + 迁移
- Consumes: `core.task_spec.parse_task_spec`、`core.task_spec.sha256_hex`（dummy zip 生成用）

迁移逻辑（`init_db` 内，幂等）：
1. 检测旧 schema（`tasks` 表含 `crate_name` 列）：`ALTER TABLE tasks RENAME TO jobs`，随后按新 schema 建 `tasks` 表。
2. 若发生了重命名且旧表有数据：生成 dummy zip（内存构造，仅含 `task.toml`，内容 `name="legacy"`、`desc="遗留数据占位 task，内容待补充"`、`image="none"`、`cmd="true"`、`workspace="/workspace"`），sha256 落盘 `{workspace}/tasks/{sha}.zip`，插入 tasks 行（同 sha 已存在则复用）。
3. 重建 `jobs` 表（新 schema：删六列、增四快照列），回填 dummy 的 `task_id/task_name/task_sha256/task_spec`。
4. `task_log_chunk_sequences` 若存在则改名 `job_file_chunk_sequences` 并把列 `task_id/log_type` 改为 `job_id/path`（重建表迁移数据）。
5. 日志文件迁移（仅当传入 `workspace` 且存在旧 `logs/` 目录）：`{id}-{type}.log` → `jobs/{id}/files/{type}.log`，目标已存在则跳过。
6. 全新部署：直接建新 schema，不创建 dummy。

- [ ] **Step 1: 写失败测试**

`test_migration.py`（用 tmp_path 手工建旧 schema 库并插入 2 条旧任务记录 + 旧日志文件）：
- 迁移后旧记录全在 `jobs`，`task_id` 指向 dummy、`task_name=="legacy"`、`task_spec` 可解析；`case_count` 等列不存在（`PRAGMA table_info(jobs)`）
- dummy 的 zip 文件存在且 `parse_task_spec` 可读
- 再次 `init_db` 幂等（仍只有 1 个 dummy）
- 旧日志 `logs/{id}-stdout.log` 落到 `jobs/{id}/files/stdout.log`
- 全新部署无 dummy（`list_task_definitions()==[]`）

`test_database.py` 更新 + 新增：
- 全部现有用例改用新方法名与字段（删掉三个统计字段的断言）
- `claim_pending_job`：更新 task 定义后 claim 到的 job 快照已刷新；task 删除后 claim 仍返回旧快照
- `record_job_file_chunk` 按 `(job_id, path)` 去重（重复 seq 返回 False）

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/unit/test_migration.py tests/unit/test_database.py -v`
Expected: FAIL

- [ ] **Step 3: 实现**

先改 `database.py`；此时 `main.py`/`scheduler.py` 会因方法改名而 import 报错——本任务**只**同步修正这些文件中的调用点名称（不改路由、不改字段语义），让单元测试能跑。`main.py` 中三个统计字段的读写暂时保留（DB 层已忽略），由 Task 3 清理。

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/test_migration.py tests/unit/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/database.py backend/tests/ backend/app/main.py backend/app/services/
git commit -m "feat(db): migrate to tasks/jobs schema with dummy task backfill"
```

### Task 3: Backend 全面改名 task→job 与统计字段清除

**Files:**
- Modify: `backend/app/main.py`、`backend/app/services/scheduler.py`、`backend/app/api/websocket.py`、`backend/app/config.py`、`backend/core/schemas.py`
- Test: `backend/tests/integration/test_api.py`、`test_runner_control_api.py`、`test_websocket.py`、`test_runner_admin_api.py`、`tests/unit/test_scheduler.py`、`test_main.py`（全部随改）

**Interfaces:**
- Produces（前端 Chunk E 依赖的契约）:
  - 路由：`/api/jobs`、`/api/jobs/{id}`、`/api/jobs/{id}/cancel|retry`、`/api/jobs/batch-*`（body 键 `job_ids`）、`/api/runners/{id}/jobs/{job_id}/sync|logs/...`、`/ws/jobs/{id}`
  - `CreateJobRequest{crate_name, version: Optional[str], task_id: int}`、`JobResponse{job_id, crate_name, version, status}`、`JobDetailResponse`（含 `task_id/task_name/task_sha256/task_spec(dict)`，无三个统计字段）
  - `RunnerTaskSyncRequest` 删除 `case_count/poc_count/compile_failed` 字段
  - WS 消息类型 `job_created/job_update/job_completed`；`ConnectionManager.connect_job/broadcast_job_update`
  - `resolve_job_file_path(config, job_id, log_type) -> Path` = `workspace/jobs/{job_id}/files/{log_type}.log`（替代 `resolve_log_path`，`RUNNER_LOG_TYPES={"stdout","stderr","runner"}`）
- Consumes: Task 2 的 Database 接口。

行为要点：
- `POST /api/jobs`：删除 crate-version 去重/复用逻辑，每次新建；校验 `task_id` 存在（404）；快照当前 `name/sha256/spec`；不再预建 workspace/logs 路径。
- job retry 前置校验 task 存在（400 "task 已删除"）；claim 与 retry 时清空 `jobs/{id}/files/` 与 `jobs/{id}/artifacts/`（本任务先内联实现，Task 4 后改用 `JobFileStore.clear`）。
- `config.py` 新增 `task_upload_max_mb=100`、`file_push_max_mb=10`、`artifact_max_mb=1024`（读 `[tasks]` 节）；`ensure_workspace_structure` 改建 `tasks/` 与 `jobs/`。
- `core/schemas.py` 的 `TaskClaimResponse` 若全库无引用则删除文件，否则更新。
- 删除 `_clear_task_logs` 的 miri_report/stats-yaml 痕迹。

- [ ] **Step 1: 更新全部受影响测试**

路由路径、请求/响应字段名、统计字段断言、WS 路径与消息类型。先运行确认大面积 FAIL。

- [ ] **Step 2: 实施改名与清除**

- [ ] **Step 3: 全量测试通过**

Run: `cd backend && uv run pytest`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/
git commit -m "refactor(backend): rename task to job and drop crate-specific stats"
```

### Task 4: `JobFileStore` 文件存取服务

**Files:**
- Create: `backend/app/services/job_files.py`
- Test: `backend/tests/unit/test_job_files.py`

**Interfaces:**
- Produces:
  - `PathSafetyError(ValueError)`
  - `JobFileStore(workspace: Path, full_mode_max_bytes: int, artifact_max_bytes: int)`
  - `files_dir(job_id) -> Path` / `artifacts_dir(job_id) -> Path`（不保证存在）
  - `safe_join(base: Path, rel: str) -> Path`（拒绝绝对路径、`..`、空路径）
  - `append_file(job_id, path, content: str) -> None`
  - `write_full(job_id, path, content: str) -> None` — 超过 `full_mode_max_bytes` 时保留尾部
  - `list_files(job_id) -> list[dict{path, size, mtime}]`（递归，按 path 排序）
  - `read_tail(job_id, path, lines: int) -> list[str]` / `read_raw(job_id, path) -> str`（不存在抛 `FileNotFoundError`）
  - `save_artifact(job_id, path, stream, ) -> int`（流式写入，超限抛 `ArtifactTooLargeError`，返回字节数）
  - `list_artifacts(job_id) -> list[dict{path, size}]` / `artifact_file_path(job_id, path) -> Path`
  - `clear(job_id) -> None`（删除 files/ 与 artifacts/ 两目录）
- Consumes: 无（`read_last_n_lines` 可复用 `app/utils/file_utils.py`）

- [ ] **Step 1: 写失败测试**

`safe_join` 拒绝 `/etc/passwd`、`a/../../b`、空串；`write_full` 超上限（测试用小上限，如 1KB）保留尾部且 size==上限；`append_file` 两次追加内容相连；`list_files`/`read_tail`/`read_raw` 正常与 404；`save_artifact` 超限抛错且不留半成品文件；`clear` 后两目录不存在。

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/test_job_files.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/services/job_files.py backend/tests/unit/test_job_files.py
git commit -m "feat(backend): add per-job file store with path safety"
```

## Chunk C: Backend API

### Task 5: task 管理 API 与 `TaskStore`

**Files:**
- Create: `backend/app/services/task_store.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_task_store.py`、`backend/tests/integration/test_task_api.py`（新建）

**Interfaces:**
- Produces:
  - `ZipValidationError(ValueError)`；`TaskStore(tasks_dir: Path).ingest_zip(zip_bytes: bytes) -> tuple[TaskSpec, str(sha256), Path]`（校验 zip 可读、条目安全、根含 `task.toml`、`parse_task_spec` 通过；写 `{sha}.zip`）；`delete_zip(sha256: str)`
  - API（写操作需 `require_admin_token`）：
    - `POST /api/tasks`（multipart `file`）→ `{id, name, desc, sha256, created_at, spec, existed: bool}`；超 `task_upload_max_mb` → 413
    - `GET /api/tasks` / `GET /api/tasks/{id}`（含 `spec` dict）
    - `PUT /api/tasks/{id}`（multipart `file`）→ 更新后记录；与另一 task 撞 sha → 409；与自身同 sha → 200 无变化
    - `DELETE /api/tasks/{id}` → 删行 + 删 zip 文件
- Consumes: `core.task_spec`（Task 1）、Database task 定义方法（Task 2）

- [ ] **Step 1: 写失败测试**

单测：`ingest_zip` 正常返回 spec+sha+落盘；无 `task.toml`/坏 TOML/zip slip 条目/损坏 zip 各抛 `ZipValidationError`。
集成（构造内存 zip 上传）：上传→列表→详情链路；重复上传 `existed=true` 且 id 相同；更新后 sha 变化、详情字段更新、与另一 task 撞 sha 返 409；删除后详情 404；无 admin token 的写操作返 401/403；超限上传 413（测试配置把上限改小）。

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/test_task_store.py tests/integration/test_task_api.py -v`

上传读流用 `UploadFile` 分块读并累计大小，超限即 413；zip 校验在内存字节上进行（上限 100MB 量级可接受）。

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/services/task_store.py backend/app/main.py backend/tests/
git commit -m "feat(backend): add task definition CRUD API"
```

### Task 6: runner 面向 API（claim 扩展、zip 下载、files 推送、artifacts 上传）

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_runner_control_api.py`（更新+新增）

**Interfaces:**
- Produces:
  - `ClaimJobResponse{job_id, crate_name, version, status, runner_id, lease_token, lease_expires_at, task_id, task_sha256}`；claim 副作用：清空 `jobs/{id}/files|artifacts`、刷新快照（DB 层已做）
  - `GET /api/runners/{id}/tasks/{task_id}/zip` → zip bytes（runner auth；404 不存在）
  - `POST /api/runners/{id}/jobs/{job_id}/files`：`RunnerFilePushRequest{lease_token, path, mode: Literal["append","full"], content, chunk_seq: Optional[int]}`；append 必带 `chunk_seq`（缺失 422），经 `record_job_file_chunk` 去重后 `append_file`；full 直接 `write_full`；响应 `{"applied": bool}`
  - `POST /api/runners/{id}/jobs/{job_id}/artifacts`：multipart（`path` 表单字段 + `file`），lease 校验，走 `JobFileStore.save_artifact`
  - 删除旧 `/logs/{log_type}[/chunks]` 推送端点
- Consumes: `JobFileStore`（Task 4）、`claim_pending_job`（Task 2）

- [ ] **Step 1: 写失败测试**

claim 响应含新字段且 files/artifacts 目录被清空；zip 下载字节一致、未知 task 404；append 顺序/乱序/重复 chunk_seq 的 `applied` 语义；full 覆盖与超上限截断；`path` 穿越 400；artifacts 上传落盘、超限 413、穿越 400；lease 错误 401/403；旧 logs 推送端点 404。

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/main.py backend/tests/
git commit -m "feat(backend): add runner file push, artifact upload and task zip APIs"
```

### Task 7: 用户面向 files/artifacts 读取 API

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/integration/test_api.py`（更新）

**Interfaces:**
- Produces（前端依赖）:
  - `GET /api/jobs/{id}/files` → `[{path, size, mtime}]`
  - `GET /api/jobs/{id}/files/{path:path}` → 默认 `{"lines": [...]}` 尾部 N 行（`?lines=`，默认 1000）；`?raw=true` 返回 PlainText 全文
  - `GET /api/jobs/{id}/artifacts` → `[{path, size}]`；`GET /api/jobs/{id}/artifacts/{path:path}` → FileResponse 下载
  - 删除旧 `GET /api/jobs/{id}/logs/...` 两端点
- Consumes: `JobFileStore`（Task 4）

- [ ] **Step 1: 写失败测试**（列表/尾部/全文/下载/404/路径穿越 400）

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/integration/test_api.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/main.py backend/tests/
git commit -m "feat(backend): add job files and artifacts read APIs"
```

## Chunk D: Runner

### Task 8: runner 配置精简与 client 更新

**Files:**
- Modify: `backend/runner/config.py`、`backend/runner/client.py`
- Test: `backend/tests/unit/runner/test_config.py`、`test_runner_client.py`

**Interfaces:**
- Produces:
  - `RunnerConfig` 删除 `max_memory_gb/max_runtime_seconds/max_cpus/docker_image` 及对应 env 解析（`RUNNER_MAX_MEMORY_GB` 等四个变量退役）
  - client 方法（路径全部改 `/api/runners/{rid}/jobs/{jid}/...`）：`claim(payload)`（响应 dict 含 `job_id/task_id/task_sha256`）、`sync_job(job_id, payload)`（改名自 `sync_task`）、`push_file(job_id, payload) -> dict`、`download_task_zip(task_id) -> bytes`（404 抛 `httpx.HTTPStatusError`）、`upload_artifact(job_id, rel_path, file_path) -> None`（httpx multipart）
  - 删除 `send_log_chunk/send_log`
- Consumes: Task 6 的 API 契约

- [ ] **Step 1: 写失败测试**（config 不再读取退役变量；client 各方法 URL/方法/载荷断言，沿用现有 mock httpx 方式）

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/runner/test_config.py tests/unit/runner/test_runner_client.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black runner/ tests/
git add backend/runner/config.py backend/runner/client.py backend/tests/unit/runner/
git commit -m "refactor(runner): slim config and update client for jobs API"
```

### Task 9: runner task 缓存 `task_cache.py`

**Files:**
- Create: `backend/runner/task_cache.py`
- Test: `backend/tests/unit/runner/test_task_cache.py`

**Interfaces:**
- Produces:
  - `TaskFetchError(RuntimeError)`
  - `TaskCache(cache_dir: Path, client)`；`async ensure(task_id: int, sha256: str) -> Path`（返回含 `task.toml` 的解压目录）
  - 行为：`{cache_dir}/{task_id}/.sha256` 与目标一致 → 直接返回；否则 per-task asyncio 锁内下载 zip、校验 `sha256_hex(bytes)`、zip slip 校验后解压到临时目录再原子替换、写 marker
  - 下载 404/网络错/hash 不符/坏 zip → `TaskFetchError`（message 明确区分 not found / mismatch）
- Consumes: `client.download_task_zip`（Task 8）、`core.task_spec.sha256_hex`（Task 1）

- [ ] **Step 1: 写失败测试**（冷下载+落盘；命中不下载；hash 不符/404/zip slip 各抛错；并发 `ensure` 同一 task 只下载一次——用 mock client 计数）

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/runner/test_task_cache.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black runner/ tests/
git add backend/runner/task_cache.py backend/tests/unit/runner/test_task_cache.py
git commit -m "feat(runner): add task zip cache with sha256 verification"
```

### Task 10: DockerRunner 按 spec 驱动

**Files:**
- Modify: `backend/runner/docker_runner.py`
- Test: `backend/tests/unit/runner/test_docker_runner.py`（更新）

**Interfaces:**
- Produces:
  - 构造：`DockerRunner(docker_pull_policy: str, mounts: list[str], log_sync_interval_seconds: float)`（删 `image/max_memory_gb/max_runtime_seconds/max_cpus`）
  - `async run(*, image: str, cmd: str, workspace_dir: Path, container_workspace: str, env: dict[str,str], cpu: Optional[float], mem: Optional[str], timeout_seconds: Optional[int], stdout_log: Path, stderr_log: Path) -> ExecutionResult`
  - 包装命令：`["sh","-c", f'exec {cmd} > "{container_workspace}/stdout.log" 2> "{container_workspace}/stderr.log"']`；env 合并 `TERM=xterm-256color`；`mem` 存在才传 `mem_limit/memswap_limit`；`cpu` 存在才传 `cpu_period=100000, cpu_quota=int(cpu*100000)`；`timeout_seconds=None` 时直接 await 不设超时
  - `ensure_image(image, pull_policy)` 改为带 image 参数
- Consumes: 现有 helper 结构（见 `docs/superpowers/plans/2026-04-19-docker-run-refactor.md` 重构后的形态）

- [ ] **Step 1: 更新/新增失败测试**（带/不带 limit 的 run_kwargs；包装命令含自定义 workspace 路径；env 合并；timeout None 分支；构造签名变化）

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/runner/test_docker_runner.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black runner/ tests/
git add backend/runner/docker_runner.py backend/tests/unit/runner/test_docker_runner.py
git commit -m "refactor(runner): drive docker run from task spec"
```

### Task 11: Reporter 通用化为文件推送器

**Files:**
- Modify: `backend/runner/reporter.py`
- Test: `backend/tests/unit/runner/test_reporter.py`（更新）

**Interfaces:**
- Produces:
  - `TaskReporter(client, job_id, lease_token, file_specs, flush_interval)`——`file_specs: dict[str, tuple[Path, str]]`，键为远端相对路径，值为 `(本地文件路径, mode)`，mode ∈ `"append"/"full"`
  - append 模式沿用偏移+`chunk_seq` 去重语义；full 模式每次全量重发；stop 后最后一次 flush 带 2s 超时（沿用现状）
- Consumes: `client.push_file`（Task 8）

- [ ] **Step 1: 更新测试**（append 增量+chunk_seq 递增；full 每轮全量；文件不存在跳过；停止时终次 flush）

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/runner/test_reporter.py -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black runner/ tests/
git add backend/runner/reporter.py backend/tests/unit/runner/test_reporter.py
git commit -m "refactor(runner): generalize reporter as file pusher"
```

### Task 12: executor 重写与 worker 调整

**Files:**
- Modify: `backend/runner/executor.py`、`backend/runner/worker.py`（claimed 键改名处）
- Test: `backend/tests/unit/runner/test_executor.py`（更新）、`test_worker.py`（更新）

**Interfaces:**
- Consumes: Task 9 `TaskCache`、Task 10 `DockerRunner`、Task 11 `TaskReporter`、`core.task_spec.parse_task_spec`（Task 1）
- Produces: `TaskExecutor.execute_claimed_job(claimed: dict) -> None`，`claimed` 键：`job_id/crate_name/version/task_id/task_sha256/lease_token`

执行流程（对应 spec 的 Runner 章节）：
1. job 目录 `{workspace_dir}/jobs/job{job_id}/`：存在则 `ensure_workspace_ownership` 后清空重建
2. `task_cache.ensure(task_id, task_sha256)`；`TaskFetchError` → terminal sync `failed` + 明确 message，直接收尾（不进 docker）
3. 解析 task.toml → `TaskSpec`
4. 下载 crate 并解压进 job 目录（复用现有 `_prepare_workspace`/`_extract_and_move_crate` 逻辑，目标目录改为 job 目录）；随后把缓存目录中除 `task.toml` 外的内容覆盖拷贝进 job 目录（`shutil.copytree(..., dirs_exist_ok=True)`）
5. reporter file_specs：`stdout.log`/`stderr.log` 指向 job 目录内对应文件（append），`runner.log` 指向 `logs/{job_id}-runner.log`（append），spec.watch 各项指向 job 目录内相对路径（full）
6. `docker.run(image=spec.image, cmd=spec.cmd, workspace_dir=job_dir, container_workspace=spec.workspace, env=spec.env, cpu/mem/timeout 取自 spec.limits, ...)`
7. 终态后：遍历 `spec.artifacts`，文件直传、目录递归传（远端 path 保持相对），缺失路径记 warning 跳过；然后走现有 terminal sync + ack 收尾
8. 删除 `_count_generated_items`/`_get_compile_failed_count` 与 sync payload 三个统计字段、`LOG_UPLOAD_CONFIG`、miri_report/stats-yaml 痕迹

- [ ] **Step 1: 更新/新增失败测试**

happy path（断言 docker.run 收到 spec 值、overlay 拷贝排除 task.toml、artifacts 逐文件上传、终态 sync 无统计字段）；`TaskFetchError` 路径（failed 终态、docker 未启动）；缺失 artifact 路径仅 warning；旧 job 目录被清空重建；worker 使用 `job_id` 键。

- [ ] **Step 2: 运行确认失败** → **Step 3: 实现** → **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/runner/ -v`

- [ ] **Step 5: Commit**

```bash
cd backend && uv run black runner/ tests/
git add backend/runner/ backend/tests/unit/runner/
git commit -m "feat(runner): execute jobs from task spec with artifacts upload"
```

## Chunk E: Frontend

### Task 13: services 层（api.js / websocket.js）

**Files:**
- Modify: `frontend/src/services/api.js`、`frontend/src/services/websocket.js`

**Interfaces:**
- Produces（页面任务依赖）:
  - jobs：`createJob(crateName, version, taskId)`、`getJobs`、`getJob(id)`、`cancelJob/deleteJob/retryJob(id)`、`batchRetry/batchDelete/batchSetPriority/batchCancel(jobIds)`、`getQueue`
  - files/artifacts：`getJobFiles(id)`、`getJobFile(id, path, {lines, raw})`、`getJobArtifacts(id)`、`artifactUrl(id, path)`
  - tasks：`getTasks()`、`getTask(id)`、`uploadTask(file)`（FormData）、`updateTask(id, file)`、`deleteTask(id)`
  - ws：`connectJob(jobId)` → `/ws/jobs/{id}`；dashboard 不变；消息类型 `job_*`
- Consumes: Task 5/6/7 的 API 契约

- [ ] **Step 1: 改写 api.js / websocket.js**（纯改名+新增，无测试框架，验证靠构建）

- [ ] **Step 2: 构建验证**

Run: `cd frontend && npm run build`
Expected: 构建成功（此时尚未改页面，引用旧方法名的页面会报错——若有，顺手动改导入名，页面大改留给 Task 15）

- [ ] **Step 3: Commit**

```bash
git add frontend/src/services/
git commit -m "refactor(frontend): rename task API to jobs and add tasks/files APIs"
```

### Task 14: 路由迁移与 Task 管理页

**Files:**
- Modify: `frontend/src/router/index.js`、`frontend/src/App.vue`（导航）
- Create: `frontend/src/views/TaskManage.vue`
- Modify: `frontend/src/views/TaskList.vue` 等文件改名（`git mv` → `JobList.vue`、`JobNew.vue`、`JobBatch.vue`、`JobDetail.vue`、`JobQueue.vue`；本任务只改名与路由，内容在 Task 15 改）

**Interfaces:**
- Produces: 路由 `/jobs`、`/jobs/new`、`/jobs/batch`、`/jobs/:id`、`/tasks`（TaskManage）；导航新增 "Tasks"（管理）入口，原 "Tasks" 入口改名 "Jobs"

TaskManage.vue 内容：上传表单（file input，成功/失败提示，成功后刷新列表）；表格列 id/name/desc/sha256(前 12 位)/created_at/updated_at；行内操作：更新（选择文件调 `updateTask`）、删除（confirm 后调 `deleteTask`）。样式沿用现有页面的 Tailwind 风格。

- [ ] **Step 1: 实现路由迁移 + git mv + TaskManage.vue**

- [ ] **Step 2: 构建验证**

Run: `cd frontend && npm run build`
Expected: 成功

- [ ] **Step 3: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): add task management page and jobs routes"
```

### Task 15: Job 页面内容改造

**Files:**
- Modify: `frontend/src/views/JobNew.vue`、`JobBatch.vue`、`JobList.vue`、`JobDetail.vue`、`JobQueue.vue`、`Dashboard.vue`、`frontend/src/utils/exportXlsx.js`、`frontend/src/views/taskListFilters.js`（若引用统计字段）

**Interfaces:**
- Consumes: Task 13 的 services 接口

内容：
- JobNew/JobBatch：挂载时 `getTasks()` 填充下拉（无 task 时提示先去管理页创建）；创建/批量创建携带 `task_id`
- JobList：删 cases/pocs/compile_failed 三列，加 task 列（`task_name` 快照）；筛选工具与 xlsx 导出同步删列
- JobDetail：删统计卡片；spec 摘要区（解析 `task_spec` JSON 展示 name/cmd/image/workspace/limits/env）；日志 tab 改用 `getJobFile(id, "stdout.log"|"stderr.log"|"runner.log")`；watch 区按 `task_spec.watch` 列文件、点击查看 `getJobFile` 内容（手动刷新按钮）；artifacts 区 `getJobArtifacts` 列表 + `artifactUrl` 下载链接
- JobQueue/Dashboard：字段名适配（`task_id`→`job_id` 等），删除任何统计字段展示

- [ ] **Step 1: 逐页改造**

- [ ] **Step 2: 构建验证 + 全库 grep 清零**

Run: `cd frontend && npm run build && grep -rn "case_count\|poc_count\|compile_failed\|caseCount\|pocCount\|compileFailed" src/ || echo "CLEAN"`
Expected: 构建成功 + CLEAN

- [ ] **Step 3: Commit**

```bash
git add frontend/src/
git commit -m "feat(frontend): rework job pages with task selector, files and artifacts"
```

## Chunk F: 收尾

### Task 16: 文档、配置样例与全量回归

**Files:**
- Modify: `config.toml.example`（新增 `[tasks]` 节三个上限键）、`CLAUDE.md`（架构与 workspace 结构描述）、`README.md`（若含旧流程描述）

- [ ] **Step 1: 更新文档与配置样例**

- [ ] **Step 2: 全量回归**

Run: `cd backend && uv run black --check app/ tests/ runner/ core/ && uv run pytest && cd ../frontend && npm run build`
Expected: 全绿

- [ ] **Step 3: 全库清零检查**

Run: `grep -rn "case_count\|poc_count\|compile_failed\|miri_report\|stats-yaml" backend/app backend/runner backend/core frontend/src || echo "CLEAN"`
Expected: CLEAN（测试与迁移文档除外，逐一人工确认）

- [ ] **Step 4: Commit**

```bash
git add config.toml.example CLAUDE.md README.md
git commit -m "docs: update config example and architecture docs for multi-task jobs"
```
