# 多 Task 支持重构设计

日期：2026-10-21
状态：已确认（brainstorming 结论）

## 背景与意图

当前平台只能运行一种固定任务：对指定 crate-version 在 docker 中执行 `cargo rapx`，并把任务特定的统计（cases / pocs / compile_failed）写进数据库和前端。

本次重构把平台泛化为**多 task 实验平台**：用户上传 task 定义（zip 包，内含 `task.toml` 元信息），然后对任意 crate-version 创建 job 来运行它。平台本身不再关心任何 task 特定的业务逻辑，只负责调度、执行、收集文件。

## 核心概念

- **Task**：一份可复用的任务定义。用户以 zip 上传，zip 的 sha256 是指纹，用于校验 backend 与 runner 两侧内容一致。task 用自增 id 作为主键（`task_id`）。
- **Job**：一次具体执行 = 在一个特定的 crate-version 上运行一个指定的 task。每次创建都生成新记录，**不再按 crate-version 去重**。runner 为每个 job 创建独立的 `job{job_id}` 文件夹作为 workspace。

## 目标 / 非目标 / 反目标

**目标**
- 支持用户上传、列表、删除 task（zip + task.toml）。
- job 创建时选择 task（单个 + 批量两个入口均保留）。
- runner 按 task.toml 的元信息（image / workspace / cmd / env / limit）在 docker 中执行 job。
- watch 文件执行中实时可查（runner 周期推送）；artifacts 在 job 结束后持久保存、可下载。
- 现有历史数据（旧 tasks 表记录与日志文件）完整保留。

**非目标**
- crates.io 下载、调度、lease/claim、WebSocket 推送的既有机制不变。
- runner 上 job 文件夹的清理策略维持现状（不新增自动清理）。
- 不做 task 的版本管理：相同 sha256 即同一 task；不同内容即新 task。

**反目标（做了就算失败）**
- 残留任何 cases / pocs / compile_failed 相关的列、字段、UI。
- 引入 task.toml 格式版本号或向前兼容逻辑（平台不考虑向前兼容）。
- 在数据库/后端中引入任何 task 特定字段（schema 必须与具体任务无关）。

## task.toml 规范

zip 根目录必须有 `task.toml`。backend 上传时解析校验，runner 执行前从自己的缓存副本中解析（两侧由 sha256 保证一致）。

```toml
# ---- 必填 ----
name = "cargo-rapx"            # 显示名，非空字符串
image = "rapx:latest"          # docker 镜像
cmd = "cargo rapx"             # 容器内执行的命令（runner 用 sh -c 包装）
workspace = "/workspace"       # 容器内 workspace 路径（绝对路径；job 文件夹挂载点）

# ---- 可选 ----
desc = "对 crate 运行 cargo rapx"
artifacts = ["stdout.log", "stderr.log", "results/"]  # 结束后收集，相对 workspace
watch = ["stdout.log", "stderr.log"]                  # 执行中周期推送，相对 workspace

[limit]                        # 整节可选；每个字段各自可选，缺失 = 不限制
cpu = 2                        # CPU 核数，正数（可为小数）
mem = "4g"                     # docker 风格内存字符串（如 "512m" / "4g"）
timeout = "2h"                 # 时长字符串（s/m/h 后缀）或整数秒

[env]                          # 可选，注入容器的环境变量
RUSTFLAGS = "-Z unstable-options"
```

校验规则（backend 上传时执行）：
- `name` / `image` / `cmd` / `workspace` 缺失或为空 → 拒绝。
- `workspace` 必须是绝对路径。
- `artifacts` / `watch` 必须是相对路径列表；拒绝绝对路径与含 `..` 的条目。
- `limit.cpu` 必须为正数；`limit.mem` 须匹配 docker 内存格式；`limit.timeout` 须为合法时长。
- 无 `schema_version` 字段，未知字段直接忽略。

## 数据库变更与迁移

### 新 schema

`tasks` 表（task 定义，全新）：
| 列 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK AUTOINCREMENT | task_id |
| name | TEXT NOT NULL | 显示名 |
| desc | TEXT | 描述 |
| sha256 | TEXT NOT NULL UNIQUE | zip 指纹 |
| zip_path | TEXT NOT NULL | zip 存储路径 |
| spec | TEXT NOT NULL | 解析后的 task.toml（JSON 字符串），供前端展示 |
| created_at | TIMESTAMP | |
| updated_at | TIMESTAMP | 最近一次 update 的时间 |

`jobs` 表（由旧 `tasks` 表演化）：
- **删除列**：`case_count`、`poc_count`、`compile_failed`、`workspace_path`、`stdout_log`、`stderr_log`（日志路径改为按 job id 约定派生，见下文；现有的 `resolve_log_path` 本来就忽略这几列）。
- **新增列**：`task_id INTEGER NOT NULL REFERENCES tasks(id)`、`task_name TEXT NOT NULL`（快照）、`task_sha256 TEXT NOT NULL`（快照）、`task_spec TEXT NOT NULL`（快照，创建时存整份解析后的 task.toml JSON）。快照保证 task 被删除后历史 job 依然完整可读（JobDetail 的 cmd / image 等摘要直接读快照，不依赖 tasks 表）。
- 其余列与索引保持不变；`task_log_chunk_sequences` 改名 `job_log_chunk_sequences`（语义不变，按 id 对应）。

### 迁移步骤（随 `init_db` 执行，幂等）

1. 检测旧 schema（`tasks` 表存在且含 `crate_name` 列）：
   - `ALTER TABLE tasks RENAME TO jobs`；
   - 创建新 `tasks` 表。
2. 创建 dummy task：生成一个只含占位 task.toml 的最小 zip（`name="legacy"`、`desc="遗留数据占位 task，内容待补充"`、`image="none"`、`cmd="true"`、`workspace="/workspace"`），计算 sha256，落盘到 `workspace/tasks/{sha256}.zip` 并插入 `tasks` 行。若同 hash 已存在则复用。**dummy task 的具体内容用户后续自行补充**；历史 job 不会再次执行，runner 永远不会拉取它。
3. 重建 `jobs` 表（SQLite 表重建模式：建新表 → INSERT SELECT → 删旧表 → 改名），回填 `task_id` / `task_name` / `task_sha256` / `task_spec` 指向 dummy task。
4. 迁移日志文件：`workspace/logs/{id}-{type}.log` 移动到 `workspace/jobs/{id}/logs/{type}.log`（目标已存在则跳过）。
5. 全新部署（无旧表）时直接按新 schema 建表，不创建 dummy task。

## Backend 变更

### 存储布局

```
workspace/
├── tasks/{sha256}.zip                  # 上传的 task zip
├── jobs/{job_id}/
│   ├── logs/{stdout,stderr,runner}.log # runner 推送的日志（含迁移来的历史日志）
│   ├── watch/{相对路径}                 # watch 文件，每次推送全量覆盖
│   └── artifacts/{相对路径}             # 结束后上传的 artifacts
└── tasks.db
```

`resolve_log_path` 改为 `workspace/jobs/{id}/logs/{log_type}.log`；`RUNNER_LOG_TYPES` 收缩为 `{stdout, stderr, runner}`。

### API

**Task 管理（用户/管理员）**
- `POST /api/tasks`：multipart 上传 zip，上传上限默认 100MB（backend config 可配 `task_upload_max_mb`；task zip 定位为脚本/配置级，KB~MB 量级）。校验通过后计算 sha256；同 hash 已存在 → 返回已有 task（200 + `existed: true`），否则入库并返回完整 task 记录。
- `GET /api/tasks`：列表（id / name / desc / sha256 / created_at）。
- `GET /api/tasks/{id}`：详情（含 spec）。
- `DELETE /api/tasks/{id}`：**总是允许删除**（即使存在引用它的 pending/running job），删除行与 zip 文件。下游影响：runner 拉取 zip 得到 404 时，job 以 failed 收场并带明确错误信息（与 sha256 不一致走同一终态路径）；对一个 task 已删除的 job 调 retry 返回 400。历史 job 的可读性由快照列保证。
- `PUT /api/tasks/{id}`：multipart 上传新 zip，替换旧定义。重新校验 task.toml，计算新 sha256，替换 zip 文件并更新 `sha256` / `spec` / `name` / `desc` / `updated_at`。新 zip 与**另一个** task 的 sha256 相同 → 409 冲突；与自身当前 sha256 相同 → 200 无变化。**pending job 在 claim 时使用 task 的当前定义**（见下文 claim 说明），即更新对已创建但未运行的 job 生效；运行中/已结束的 job 不受影响。这也是补充 dummy task 真实内容的通道。
- 上传、更新与删除使用 admin token 鉴权（沿用 `require_admin_token`），列表/详情公开。

**Job（原 `/api/tasks/*` 业务接口整体改名为 `/api/jobs/*`）**
- `POST /api/jobs`：body `{crate_name, version?, task_id}`。version 缺省走 crates.io 查最新版（逻辑不变）。**不再做 crate-version 去重/复用**，每次新建 job；校验 task_id 存在并快照 name/sha256。
- `GET /api/jobs`、`GET /api/jobs/{id}`、`POST /api/jobs/{id}/cancel`、`POST /api/jobs/{id}/retry`、`DELETE /api/jobs/{id}`、`POST /api/jobs/batch-retry|batch-delete|batch-priority|batch-cancel`：语义不变，仅改名与去字段。
- `GET /api/jobs/{id}/logs/{log_name}[/raw]`：不变（路径模板改名）。
- `GET /api/jobs/{id}/watch`：列出 watch 文件（path / size / 更新时间）；`GET /api/jobs/{id}/watch/{path:path}`：读最新内容。
- `GET /api/jobs/{id}/artifacts`：列出 artifacts（path / size）；`GET /api/jobs/{id}/artifacts/{path:path}`：下载。
- `GET /api/queue`、`/api/dashboard/*`：不变。

**Runner 面向（runner token + lease 校验不变）**
- `POST /api/runners/{id}/claim`：响应字段 `id` 改名 `job_id`，新增 `task_id`、`task_sha256`。`task_sha256` 取 **claim 时刻 tasks 表的当前值**（而非 job 创建时的快照），使 task update 对 pending job 生效；claim 成功时把 job 的 `task_name` / `task_sha256` / `task_spec` 快照刷新为当前值，保证快照语义 = "该 job 实际运行所用的定义"。
- `GET /api/runners/{id}/tasks/{task_id}/zip`：下载 task zip（bytes）。
- `POST /api/runners/{id}/jobs/{job_id}/sync`：路径改名；payload 删除 `case_count` / `poc_count` / `compile_failed`。
- `POST /api/runners/{id}/jobs/{job_id}/logs/{log_type}[/chunks]`：路径改名，语义不变。
- `POST /api/runners/{id}/jobs/{job_id}/watch`：JSON `{lease_token, path, content}`，全量覆盖写入 `jobs/{id}/watch/{path}`。
- `POST /api/runners/{id}/jobs/{job_id}/artifacts`：multipart 上传（`path` 表单字段 + 文件），写入 `jobs/{id}/artifacts/{path}`；每个 artifact 文件一次请求。

**路径安全**：watch / artifacts 的 `path` 一律做规范化并校验落点必须位于对应目录内（拒绝绝对路径、`..`）。

**WebSocket**：`/ws/tasks/{id}` → `/ws/jobs/{id}`；dashboard 消息类型 `task_*` → `job_*`。

### Scheduler / 服务层

`TaskScheduler`、`recover_orphaned_tasks`、`reconcile_stale_tasks`、lease 逻辑语义不变，仅随表名/字段改名。`Database` 方法相应改名（`create_task`→`create_job` 等），`apply_task_sync` 删除三个统计参数。

## Runner 变更

### 目录布局

```
{workspace_dir}/
├── jobs/job{job_id}/        # job workspace：crate 源码 + zip 覆盖内容，挂载进容器
├── task_cache/{task_id}/    # task 缓存：task.zip + extracted/ + .sha256
└── logs/                    # runner 本地日志（现状不变）
```

### 执行流程

1. claim 拿到 `{job_id, crate_name, version, task_id, task_sha256, lease_token}`。
2. task 缓存：`task_cache/{task_id}/` 存在且记录的 sha256 与 claim 一致 → 复用；否则从 backend 下载 zip，**校验下载字节的 sha256 与 claim 一致**。下载失败（404/网络错误）或 hash 不一致 → job 直接走 terminal sync 置为 failed，错误信息明确（如 "task zip not found on backend" / "task zip sha256 mismatch"），不进入 docker 执行阶段。
3. 从缓存副本解析 task.toml。
4. 准备 job 目录：清空重建 `jobs/job{job_id}/`；下载 crate 并解压到该目录（现有 `CrateDownloader` 逻辑不变）；再把 zip 中除 `task.toml` 外的内容**覆盖拷贝**到同一目录（同名文件以 zip 为准；解压时拒绝绝对路径与 `..` 条目）。
5. 按 spec 构造 per-job `DockerRunner`：`image`、`working_dir = spec.workspace`、job 目录挂载到 `spec.workspace`、`cmd` 用 `sh -c` 包装并把 stdout/stderr 重定向到 `{workspace}/stdout.log` / `{workspace}/stderr.log`（沿用现状）；`env` = spec.env + `TERM=xterm-256color`；`limit` 各项缺失则不加对应 docker 限制；`timeout` 缺失则不设超时。镜像拉取策略沿用 runner 配置。
6. Reporter：stdout / stderr / runner 三个 chunk 模式日志不变；`watch` 列表中的文件以全量模式周期性推送到 watch endpoint，周期沿用 `log_flush_interval_seconds`。
7. 终态后：按 `artifacts` 列表逐个上传（目录则递归其下文件；缺失路径记 warning 跳过），然后走现有 terminal sync + ack 流程。
8. 删除 `_count_generated_items` / `_get_compile_failed_count` 及 sync payload 中的对应字段。

### 配置变更

退役：`RUNNER_MAX_MEMORY_GB`、`RUNNER_MAX_RUNTIME_SECONDS`、`RUNNER_MAX_CPUS`、`RUNNER_DOCKER_IMAGE`（限制与镜像全部由 task.toml 决定）。
保留：server 认证、各类 interval、`RUNNER_MAX_JOBS`（并发）、`RUNNER_DOCKER_PULL_POLICY`、`RUNNER_DOCKER_MOUNTS`、`RUNNER_WORKSPACE_DIR`、crates.io 策略项。

## 前端变更

- **新增 Task 管理页**（`/tasks`）：上传 zip（成功后展示解析出的 name / cmd / image 等摘要）、task 列表（id / name / desc / sha256 截断显示 / 创建时间 / 更新时间）、更新（上传替换 zip）、删除。
- **Job 创建**（原 TaskNew / TaskBatch）：增加 task 下拉选择（数据来自 `GET /api/tasks`），其余交互不变；批量页仍逐条调创建接口。
- **Job 列表**（TaskList → JobList）：删除 cases / pocs / compile_failed 三列，新增 task 列（显示快照名）。
- **Job 详情**（TaskDetail → JobDetail）：删除三个统计卡片；展示 task 名、cmd、image 等 spec 摘要；保留日志查看（stdout / stderr / runner）；新增 watch 文件查看（列表 + 内容，可刷新）与 artifacts 浏览（列表 + 下载）。
- **Dashboard**：统计口径保持纯状态计数（本就不含 task 特定字段）；清理任何 cases/pocs 展示。
- **exportXlsx.js**：移除 cases / pocs / compile_failed 相关导出列。
- api.js 全面改名（task→job + 新增 task 管理接口）；websocket.js 改 `/ws/jobs`；路由与组件文件名随之一并改为 Job*。

## 我已自行敲定的决定（供审查）

1. watch / artifacts 不复用 `logs/{log_type}` 通道，而是新增独立 endpoint 家族（`log_type` 路径参数无法安全携带任意相对路径），但鉴权（lease）、推送节奏、全量覆盖语义与现有日志通道完全一致。
2. 容量上限：watch 单文件推送上限 512KB（超出截断尾部），artifacts 单文件上限 200MB，均为 backend config 可配项。
3. task 上传/删除走 admin token；列表/详情/zip 下载（runner 接口）按各自现有鉴权。
4. claim 响应字段 `id` → `job_id`，runner 同步改名（runner 与 backend 一起部署，无兼容负担）。
5. 后端日志文件迁入 `workspace/jobs/{id}/logs/`，目录结构统一按 job 组织。
6. 容器默认环境变量只保留 `TERM=xterm-256color`；原硬编码的 `CARGO_TERM_COLOR` 移除，由需要它的 task 在自己的 `[env]` 中声明。
7. stdout/stderr 仍重定向到容器内 workspace 根下的 `stdout.log` / `stderr.log`，task 作者通过 watch / artifacts 引用它们。
8. 相同 sha256 重复上传返回已有 task（HTTP 200 + `existed: true`），不视为错误。
9. job 的 retry 沿用现有 `reset_task_for_retry` 语义（重置状态、清空日志与 chunk 序号），并扩展为同时清空 backend 侧 `jobs/{id}/watch/` 与 `jobs/{id}/artifacts/` 目录，避免上一次尝试的旧产物混入；retry 前置校验 task 仍存在，已删除则返回 400。
10. priority 与四个 batch 操作保留原语义。
11. task 删除总是允许（用户已确认）；遗留 dummy task 在用户通过 `PUT /api/tasks/{id}` 补充真实内容后，历史 job 的 retry 自然恢复可用，无需特殊处理。
12. task update 的生效范围：claim 时取 task 当前 sha256 并刷新 job 快照，即 pending job 自动用上新定义；运行中/已结束的 job 不受影响；不同 task 间 sha256 撞车返回 409。

## 留给 builder 的部分

- 具体代码组织、组件结构与样式细节。
- 测试粒度与命名（遵循仓库现有 TDD 习惯）。
- 错误消息的具体文案。
- `limit.mem` / `limit.timeout` 解析的具体实现（按上述格式约定）。
