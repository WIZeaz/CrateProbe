# Runner 监控与任务界面优化设计文档

## 目标

本次改造目标是将前端“资源监视”升级为“Runner 监视”，并在任务相关页面统一体现 Runner 信息，具体包括：

1. Dashboard 从主机级 `SystemMonitor` 升级为 Runner 级监控，展示健康状态与资源占用。
2. Queue、TaskList、TaskDetail 展示 Runner 信息（仅 badge 形式显示 runner_id）。
3. Runner 页面增加详情区域，展示资源监控曲线。

## 非目标

- 不引入时序数据库。
- 不对 runner metrics 做持久化存储（重启后允许丢失历史指标）。
- 不新增 `idle` 健康状态，`idle` 统一归入 `online`。

## 已确认决策

- Runner 资源上报使用独立接口（不复用 heartbeat）。
- 采样间隔为 10 秒。
- 曲线窗口支持 `1h / 6h / 24h`，默认 `1h`。
- 默认曲线指标：`CPU%`、`内存%`、`磁盘%`、`活跃任务数`。
- Queue / TaskList / TaskDetail 中 Runner 信息只显示为 runner_id badge，不显示状态文字。

## 总体方案

采用“Runner 上报 + 服务端内存缓存 + 管理端查询”的轻量链路：

1. runner 进程每 10 秒采集一次本机指标并调用 `POST /api/runners/{runner_id}/metrics` 上报。
2. 后端在进程内 `RunnerMetricsStore` 保存最近 24 小时指标（按 runner_id 分桶）。
3. 前端通过 admin 查询接口拉取概览与时序数据，完成 Dashboard 和 Runner 页渲染。

该方案实现成本低，满足“前端可展示”目标，同时不改动数据库 schema。

## 后端设计

### 1) 健康状态模型

健康状态统一由后端计算并下发：

- `disabled`: runner.enabled 为 false
- `offline`: `now - last_seen_at > config.runner_offline_seconds`
- `online`: 其余情况（包含原 idle）

### 2) 内存指标存储

新增服务模块：`backend/app/services/runner_metrics_store.py`

- 内部结构：`dict[str, deque[RunnerMetricPoint]]`
- 并发控制：`asyncio.Lock`
- 保留窗口：固定 24 小时（超出窗口的点在写入与查询时剪枝）
- 时间来源：优先使用上报 `timestamp`，异常或缺失时回退服务端当前时间

建议数据结构：

```python
@dataclass
class RunnerMetricPoint:
    ts: datetime
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    active_tasks: int
```

### 3) API 设计

#### 3.1 Runner 上报接口（Runner Token 鉴权）

`POST /api/runners/{runner_id}/metrics`

请求体：

```json
{
  "timestamp": "2026-04-14T11:20:30Z",
  "cpu_percent": 37.5,
  "memory_percent": 62.1,
  "disk_percent": 41.0,
  "active_tasks": 1
}
```

返回：

```json
{ "success": true }
```

校验规则：

- 百分比字段范围 `[0, 100]`
- `active_tasks >= 0`
- timestamp 可选

#### 3.2 Runner 概览接口（Admin Token 鉴权）

`GET /api/admin/runners/overview`

返回每个 runner 的：

- 基础信息：`runner_id`, `enabled`, `last_seen_at`
- 健康状态：`health_status` (`online|offline|disabled`)
- 最新指标：`latest_metrics`（可能为 null）

该接口仅用于 Dashboard 卡片和 Runner 页面概览，不用于任务页面渲染。

#### 3.3 Runner 时序接口（Admin Token 鉴权）

`GET /api/admin/runners/{runner_id}/metrics?window=1h|6h|24h`

返回：

- `runner`: 基础信息 + `health_status`
- `window`: 实际窗口
- `latest`: 最新点（可空）
- `series`: 点序列（时间升序）

### 4) Runner 进程改造

涉及文件：

- `backend/app/runner/client.py`
- `backend/app/runner/worker.py`
- `backend/app/runner/config.py`

改造点：

1. `RunnerControlClient` 新增 `send_metrics()` 调用 `/metrics`。
2. 新增资源采集器（建议使用 `psutil`），采集 CPU/内存/磁盘。
3. worker 在 `run_forever()` 内按固定周期上报 metrics。
4. `active_tasks` 由 worker 内部执行状态计算（当前实现通常为 0/1，保留字段通用性）。
5. 增加配置项：`RUNNER_METRICS_INTERVAL_SECONDS`（默认 10）。

### 5) 任务接口契约补充（用于 badge 展示）

为满足 Queue / TaskList / TaskDetail 的 runner_id badge 展示，后端任务相关响应需统一包含 `runner_id` 字段（可空）：

- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/queue`
- 任务相关 WebSocket 推送 payload（task_update/task_created/task_completed）

前端展示约定：

- `runner_id` 非空：渲染为 badge 文本
- `runner_id` 为空：显示 `-`

### 6) 容错策略

- metrics 上报失败不影响任务执行主流程；记录 warning 后继续下次重试。
- 若前端查询到 `latest_metrics = null`，视为“暂无监控数据”。
- 服务重启后缓存清空，接口返回空序列，不报错。

## 前端设计

### 1) API 层扩展

文件：`frontend/src/services/api.js`

新增方法：

- `getRunnerOverview()` -> `GET /api/admin/runners/overview`
- `getRunnerMetrics(runnerId, window)` -> `GET /api/admin/runners/{runnerId}/metrics`

### 2) Dashboard 改造

文件：`frontend/src/views/Dashboard.vue`

改造方向：

- 用 `RunnerMonitorPanel` 替换当前 `SystemMonitor` 区块。
- 每 10 秒刷新一次 overview 数据（与采样频率一致）。
- 每个 runner 显示：
  - `runner_id`
  - `health_status` badge（online/offline/disabled）
  - latest `cpu/memory/disk/active_tasks`

### 3) Queue / TaskList / TaskDetail 的 Runner 展示

文件：

- `frontend/src/views/TaskQueue.vue`
- `frontend/src/views/TaskList.vue`
- `frontend/src/views/TaskDetail.vue`

改造规则：

- 统一用 badge 渲染 runner_id（例如圆角浅底标签）。
- 仅显示 ID，不拼接状态文本。
- 无 runner_id 时显示 `-`。
- 与现有表格风格一致，避免列宽抖动。

### 4) Runner 页面增强

文件：`frontend/src/views/RunnerList.vue`

改造方向：

1. 维持 runner 列表管理能力（创建/删除）不变。
2. 增加“详情面板”（行内展开或侧栏二选一，默认行内展开），展示：
   - Runner 基础信息
   - 健康状态
   - 窗口切换器（`1h/6h/24h`）
   - 四条资源曲线（CPU/内存/磁盘/活跃任务数）
3. 曲线默认加载 `1h`，切换窗口重新请求。

图表实现建议：

- 优先使用轻量实现（如原生 SVG 折线组件）减少依赖；
- 若可维护性不足，再引入小型图表库。

### 5) 移动端与响应式

- Dashboard 的 runner 卡片在小屏采用单列堆叠。
- Runner 详情曲线区域在移动端使用纵向排列，保证可读性。
- TaskList 虚拟列表新增 runner badge 列时保证最小可视宽度。

## 数据流

1. Runner 周期采样（10s）
2. Runner -> `/api/runners/{runner_id}/metrics` 上报
3. 后端写入 `RunnerMetricsStore`（内存 deque）
4. 前端：
   - Dashboard -> `/api/admin/runners/overview`
   - Queue / TaskList / TaskDetail -> 直接使用任务接口中的 `runner_id` 字段渲染 badge
   - Runner 详情 -> `/api/admin/runners/{runner_id}/metrics?window=...`
5. 前端渲染 badge、概览与曲线

## 错误处理

- runner token 无效：上报接口返回 403。
- admin token 无效：overview/metrics 查询返回 403，前端沿用现有 admin token 错误提示机制。
- runner 指标为空：曲线展示空态文案，不抛异常。
- window 非法值：后端返回 422。

## 测试与验收

### 后端测试

1. `RunnerMetricsStore`：写入、剪枝、窗口过滤、空数据场景。
2. `/metrics`：鉴权、字段校验、写入成功。
3. `/admin/runners/overview`：状态计算正确（online/offline/disabled）。
4. `/admin/runners/{id}/metrics`：窗口返回正确。

### 前端验证

1. Dashboard 正常显示 runner 健康与 latest 资源。
2. Queue / TaskList / TaskDetail 正确显示 runner_id badge。
3. Runner 页可查看详情和四条曲线，并支持 `1h/6h/24h` 切换。
4. 无指标、离线 runner、无 runner_id 任务等边界场景显示正常。
5. `npm run build` 通过。

## 风险与权衡

- 内存存储会在服务重启后丢失历史数据（已接受）。
- 24h 多 runner 高频采样可能增加内存占用，需通过剪枝控制。
- runner 与服务端时间不一致可能导致曲线时间轴偏差，需在上报入口做时间容错。

## 验收标准

1. Dashboard 不再展示旧的主机级 system monitor，改为 runner 监视面板。
2. runner 监视面板可显示健康状态和资源占用（latest）。
3. Queue / TaskList / TaskDetail 均显示 runner_id badge。
4. Runner 页面可展示 runner 详情，并可查看 `1h/6h/24h` 资源曲线。
5. 健康状态仅包含 `online/offline/disabled`。
