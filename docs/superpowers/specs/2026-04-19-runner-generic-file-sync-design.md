# Runner/Backend 通用文件同步接口设计（增量 + 全量）

## 背景

当前 runner 到 backend 的文件上传能力是基于日志类型（`log_type`）的增量 chunk 接口，路径与类型存在强绑定：

- 接口：`POST /api/runners/{runner_id}/tasks/{task_id}/logs/{log_type}/chunks`
- 后端通过 `log_type` 白名单 + resolver 决定落盘位置
- `stats.yaml` 也走增量上传

这与当前需求不匹配：

1. 需要同时支持两种上传语义：
   - 增量（适用于 append-only 文件）
   - 全量覆盖（适用于每次重生成的小文件）
2. 接口需与具体文件解耦，上传对象由 request 中 `file_name` 指定
3. backend 不做 allowlist 校验
4. 所有上传文件统一落在 backend `workspace/logs` 目录，并使用 `{task_id}-{file_name}` 命名
5. 将 `stats.yaml` 迁移为全量上传，其它继续增量
6. 维护 runner 环境变量：移除过时变量，新增文件同步周期变量

## 目标

- 提供两种通用文件上传接口（增量 / 全量），不依赖路径参数中的文件类型
- 通过 `file_name` 指定上传对象，但由 backend 统一决定物理落盘规则
- 所有 runner 上传文件统一存放在 `workspace/logs/`，并用 `{task_id}-` 前缀保证任务隔离
- `stats.yaml` 使用全量覆盖上传；其它文件继续增量
- 保持现有任务租约、鉴权与幂等机制

## 非目标

- 不引入 backend 侧 allowlist
- 不改变 runner 内部文件生成位置与生成逻辑
- 不改动前端交互模式（仍按现有日志读取 API 获取内容）

## 总体方案

采用双接口（方案 A）：

1. 增量接口（append）
   - `POST /api/runners/{runner_id}/tasks/{task_id}/files/chunks`
2. 全量接口（overwrite）
   - `PUT /api/runners/{runner_id}/tasks/{task_id}/files`

两者都通过 request body 提供 `file_name`，并沿用 lease token 校验。

## API 设计

### 1) 增量文件上传

`POST /api/runners/{runner_id}/tasks/{task_id}/files/chunks`

请求体：

```json
{
  "lease_token": "...",
  "file_name": "stdout.log",
  "chunk_seq": 12,
  "content": "..."
}
```

响应体：

```json
{
  "appended": true
}
```

语义：

- `chunk_seq` 对同一 `(task_id, file_name)` 单调递增
- backend 使用数据库进行幂等去重（重复/过期 chunk 不重复写）
- 写入目标文件：`workspace/logs/{task_id}-{file_name}`（append）

状态码与返回约定：

- 追加成功：`200`, `{"appended": true}`
- 重复/过期 chunk（`chunk_seq <= last_chunk_seq`）：`200`, `{"appended": false, "reason": "stale_or_duplicate"}`
- 非法 `file_name` 或 `chunk_seq < 1`：`400`
- lease/runner 鉴权失败：沿用现有错误码（403/409）

### 2) 全量文件上传

`PUT /api/runners/{runner_id}/tasks/{task_id}/files`

请求体：

```json
{
  "lease_token": "...",
  "file_name": "stats.yaml",
  "content": "..."
}
```

响应体：

```json
{
  "updated": true,
  "bytes": 123
}
```

语义：

- 每次请求直接覆盖目标文件
- 不使用 `chunk_seq`
- 写入目标文件：`workspace/logs/{task_id}-{file_name}`（overwrite）

## backend 设计

### 请求模型

- 新增 `RunnerTaskFileChunkRequest`：`lease_token`, `file_name`, `chunk_seq`, `content`
- 新增 `RunnerTaskFileFullRequest`：`lease_token`, `file_name`, `content`

### 文件名与路径规则

`file_name` 是逻辑标识，不是路径：

- 允许：单一文件名（如 `stdout.log`, `stats.yaml`）
- 禁止：空值、包含 `/`、`\\`、`..`

落盘函数：

- `resolve_task_file_path(task_id, file_name, config) -> Path`
- 固定返回：`config.workspace_path / "logs" / f"{task_id}-{file_name}"`

### 数据库幂等记录

复用现有 `task_log_chunk_sequences` 表，不做 schema 迁移：

- 表结构维持不变（列名继续是 `log_type`）
- 语义升级为“存储 `file_name` 字符串”
- 代码层方法名改为 `record_task_file_chunk(task_id, file_name, chunk_seq)`，内部仍写入该列
- 主键继续是 `(task_id, log_type)`，等价承载 `(task_id, file_name)` 幂等键
- 发布策略：backend 与 runner 同步发布，不承诺新 runner 与旧上传协议长期并行

### 清理与读取兼容

- `_clear_task_logs` 采用唯一策略：按 `workspace/logs/{task_id}-*` 前缀清理（不依赖文件名集合）
- 清理同一个 task 时，同步删除该 task 的 chunk 幂等记录（`task_log_chunk_sequences` 中 `task_id = ?` 的所有记录）
- `/api/tasks/{task_id}/logs/{log_name}` 与 `/raw` 的读取逻辑改为解析 `log_name -> file_name` 后读取 `workspace/logs/{task_id}-{file_name}`
- 读取映射表（固定、显式）：

| `log_name` | `file_name` |
|---|---|
| `stdout` | `stdout.log` |
| `stderr` | `stderr.log` |
| `runner` | `runner.log` |
| `miri_report` | `miri_report.txt` |
| `stats-yaml` | `stats.yaml` |

- 归一化规则：不做大小写转换，不做下划线/连字符自动替换，不做扩展名推断；仅接受表内键，未知值返回 404

## runner 设计

### 上报模式拆分

`TaskReporter` 内部拆分两类上传对象：

- `incremental_files`：`stdout.log`, `stderr.log`, `runner.log`, `miri_report.txt`
- `full_files`：`stats.yaml`

### 增量上报

- 延续现有 offset + chunk_seq 机制
- key 从旧 `log_type` 改为 `file_name`
- 调用 client 新方法 `send_file_chunk(...)`

### 全量上报

- 周期检查 `stats.yaml` 内容
- 仅内容变化时调用 `upload_file_full(...)` 覆盖上传
- 不使用 chunk_seq

### 与 runner 现有逻辑关系

- runner 本地文件生成位置不变
- 仅上传协议从 `log_type` 变更为 `file_name`
- `stats.yaml` 从增量通道迁移到全量通道

## 环境变量与配置

### 新增

- `RUNNER_FILE_SYNC_INTERVAL_SECONDS`
  - 含义：runner 向 backend 执行文件同步（增量+全量）的周期
  - 默认：`30`

### 移除（过时）

- `RUNNER_LOG_FLUSH_INTERVAL_SECONDS`
  - 被 `RUNNER_FILE_SYNC_INTERVAL_SECONDS` 替代

### 保留

- `RUNNER_LOG_SYNC_INTERVAL_SECONDS`
  - 该变量用于 docker 内日志同步到 runner 本地文件，语义不同，继续保留

### 代码映射

- `RunnerConfig.log_flush_interval_seconds` -> `RunnerConfig.file_sync_interval_seconds`
- `TaskReporter(..., log_flush_interval=...)` -> `TaskReporter(..., file_sync_interval=...)`

## 错误处理

- lease 校验失败：返回 409/403（沿用现有策略）
- 无效 `file_name`：返回 400
- chunk 重放/乱序：`appended=false`，不写文件
- 全量写入失败（IO 异常）：返回 500，并记录结构化错误日志
- 网络抖动：runner 保持现有重试节奏（下个周期继续尝试）

## 测试策略

### backend 单元/集成

- 增量接口：首次写入成功，重复 `chunk_seq` 幂等
- 全量接口：多次上传后内容等于最后一次
- 路径规则：所有上传均落在 `workspace/logs/{task_id}-{file_name}`
- `file_name` 校验：`""`, `"../x"`, `"a/b"`, `"a\\b"` 均返回 400
- 读取接口：`stats-yaml` / `miri_report` 能读取对应新落盘文件
- 新接口 lease/auth：覆盖 403/409 场景（无 token、错误 token、lease 不匹配、租约过期）
- 清理逻辑：`_clear_task_logs` 会删除 `{task_id}-*` 文件并清空该 task 的 chunk 记录
- 数据库兼容：旧 schema 直接可运行新逻辑（不迁移列名），幂等行为不变

### runner 单元

- 增量文件继续使用 offset + chunk_seq
- `stats.yaml` 改走全量上传
- 内容不变不重复上传；内容变化触发覆盖上传
- 配置解析：`RUNNER_FILE_SYNC_INTERVAL_SECONDS` 默认值、生效值、非法值校验
- 过时配置：`RUNNER_LOG_FLUSH_INTERVAL_SECONDS` 不再生效（移除后不影响启动）

### 端到端

- 执行任务时验证：
  - `stdout/stderr/runner/miri_report` 在 backend 端持续增长
  - `stats.yaml` 被覆盖更新，最终内容为最后一次生成内容

## 迁移与发布

建议 backend 与 runner 同步发布，避免协议错配。

迁移步骤：

1. backend 发布新接口与新路径规则
2. runner 发布新上传逻辑（`file_name` + 全量接口）
3. 验证 `stats.yaml` 在前端显示正常（通过现有日志读取 API）
4. 清理旧 `log_type` 上传路径与相关兼容分支（如存在）

## 风险与缓解

- 风险：不做 allowlist 可能被 runner 侧误传文件名
  - 缓解：严格文件名格式校验 + lease 绑定 + 目录固定在 `workspace/logs`
- 风险：backend/runner 版本不一致
  - 缓解：同步发布，或短期保留旧接口兼容窗口
- 风险：全量上传过于频繁
  - 缓解：runner 侧仅在内容变化时上传

## 验收标准

- 存在两种通用文件上传接口（增量/全量），均通过 body `file_name` 指定对象
- backend 不依赖 allowlist 判断可上传类型
- 所有上传文件统一落盘到 `workspace/logs/{task_id}-{file_name}`
- `stats.yaml` 通过全量接口上传，其它文件通过增量接口上传
- runner 配置完成环境变量迁移：移除 `RUNNER_LOG_FLUSH_INTERVAL_SECONDS`，新增 `RUNNER_FILE_SYNC_INTERVAL_SECONDS`
- 相关单元测试与集成测试通过
