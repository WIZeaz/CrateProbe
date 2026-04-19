# Runner Monitoring UI Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为平台新增基于 Runner 的实时监控链路（内存缓存，不落库），并完成 Dashboard、Queue、TaskList、TaskDetail、Runner 页面的 Runner 信息与监控曲线展示。

**Architecture:** 后端新增独立 runner metrics 上报与查询 API，使用进程内 `RunnerMetricsStore` 维护 24 小时滑动窗口数据；健康状态由后端统一计算为 `online/offline/disabled`。前端通过 admin 查询接口渲染 Dashboard 和 Runner 详情曲线，任务相关页面仅使用 `runner_id` badge，不依赖 admin 概览接口。

**Tech Stack:** FastAPI, Pydantic, asyncio, psutil, pytest, Vue 3 (Composition API), Axios, Tailwind CSS, Vite.

---

## 文件结构与职责分解

- `backend/app/services/runner_metrics_store.py`（新增）
  - 内存时序存储，负责写入、剪枝、按窗口查询、latest 查询。
- `backend/app/main.py`（修改）
  - 新增 metrics request/response 模型。
  - 新增 `/api/runners/{runner_id}/metrics`、`/api/admin/runners/overview`、`/api/admin/runners/{runner_id}/metrics`。
  - 将 `runner_id` 纳入任务 HTTP 与 WebSocket payload。
- `backend/app/runner/client.py`（修改）
  - 新增 `send_metrics()`。
- `backend/app/runner/config.py`（修改）
  - 增加 `metrics_interval_seconds` 配置，读取 `RUNNER_METRICS_INTERVAL_SECONDS`。
- `backend/app/runner/worker.py`（修改）
  - 采集并上报 CPU/内存/磁盘/active_tasks。
- `backend/tests/unit/test_runner_metrics_store.py`（新增）
  - 覆盖 store 行为（写入、窗口过滤、剪枝、并发写入）。
- `backend/tests/integration/test_runner_control_api.py`（修改）
  - 增加 metrics 接口鉴权、参数校验、窗口查询、健康状态分支验证。
- `backend/tests/unit/test_runner_client.py`（修改）
  - 增加 `send_metrics()` 测试。
- `backend/tests/unit/test_runner_worker.py`（修改）
  - 验证 worker 上报 metrics 与失败容错。
- `backend/tests/integration/test_api.py`（修改）
  - 验证 `GET /api/tasks`、`GET /api/tasks/{id}`、`GET /api/queue` 均返回 `runner_id`。
- `backend/tests/integration/test_websocket.py`（修改）
  - 验证 task websocket 初始与更新 payload 均含 `runner_id`。
- `frontend/src/components/RunnerIdBadge.vue`（新增）
  - 统一渲染 runner_id badge（含空值 fallback）。
- `frontend/src/components/RunnerMonitorPanel.vue`（新增）
  - Dashboard runner 概览卡片。
- `frontend/src/components/RunnerMetricsChart.vue`（新增）
  - 轻量 SVG 曲线组件（支持多序列中的单序列渲染）。
- `frontend/src/services/api.js`（修改）
  - 新增 runner overview/metrics 查询方法。
- `frontend/src/views/Dashboard.vue`（修改）
  - 移除 `SystemMonitor`，接入 `RunnerMonitorPanel`。
- `frontend/src/views/TaskQueue.vue`（修改）
  - runner 列改为 badge 展示。
- `frontend/src/views/TaskList.vue`（修改）
  - 新增 runner badge 列。
- `frontend/src/views/TaskDetail.vue`（修改）
  - runner 信息改为 badge 展示。
- `frontend/src/views/RunnerList.vue`（修改）
  - 增加详情区、窗口切换、四条曲线。

---

## Chunk 1: Backend metrics pipeline + contract

### Task 1: 实现 `RunnerMetricsStore`（TDD）

**Files:**
- Create: `backend/app/services/runner_metrics_store.py`
- Create: `backend/tests/unit/test_runner_metrics_store.py`

- [ ] **Step 1: 写失败测试 - latest/window 行为**

```python
@pytest.mark.asyncio
async def test_store_returns_latest_and_window_filtered_series():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await store.append("runner-1", now - timedelta(hours=2), 10.0, 20.0, 30.0, 0)
    await store.append("runner-1", now - timedelta(minutes=30), 40.0, 50.0, 60.0, 1)

    latest = await store.get_latest("runner-1")
    assert latest is not None
    assert latest.cpu_percent == 40.0

    one_hour = await store.get_series("runner-1", timedelta(hours=1))
    assert len(one_hour) == 1
    assert one_hour[0].active_tasks == 1
```

- [ ] **Step 2: 写失败测试 - 24h 剪枝行为**

```python
@pytest.mark.asyncio
async def test_store_prunes_points_older_than_max_age():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await store.append("runner-1", now - timedelta(hours=25), 1, 1, 1, 0)
    await store.append("runner-1", now - timedelta(hours=1), 2, 2, 2, 0)

    series = await store.get_series("runner-1", timedelta(hours=24))
    assert len(series) == 1
    assert series[0].cpu_percent == 2
```

- [ ] **Step 3: 写失败测试 - 并发写入行为**

```python
@pytest.mark.asyncio
async def test_store_handles_concurrent_writes():
    now = datetime(2026, 4, 15, 12, 0, 0)
    store = RunnerMetricsStore(max_age=timedelta(hours=24), now_fn=lambda: now)

    await asyncio.gather(
        *(store.append("runner-1", now, float(i), 10.0, 20.0, 0) for i in range(50))
    )

    series = await store.get_series("runner-1", timedelta(hours=1))
    assert len(series) == 50
```

- [ ] **Step 3.1: 写失败测试 - 查询路径也会执行剪枝**

```python
@pytest.mark.asyncio
async def test_store_prunes_old_points_on_query_without_new_append():
    base = datetime(2026, 4, 15, 12, 0, 0)
    now_ref = {"now": base}
    store = RunnerMetricsStore(
        max_age=timedelta(hours=24), now_fn=lambda: now_ref["now"]
    )

    await store.append("runner-1", base - timedelta(hours=23), 10, 10, 10, 0)
    await store.append("runner-1", base - timedelta(hours=1), 20, 20, 20, 0)

    now_ref["now"] = base + timedelta(hours=2)
    series = await store.get_series("runner-1", timedelta(hours=24))
    assert len(series) == 1
    assert series[0].cpu_percent == 20
```

- [ ] **Step 4: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/unit/test_runner_metrics_store.py -v`
Expected: FAIL（模块不存在）。

- [ ] **Step 5: 实现 `RunnerMetricPoint` 数据结构**

在 `backend/app/services/runner_metrics_store.py` 新建 dataclass：`ts/cpu_percent/memory_percent/disk_percent/active_tasks`。

- [ ] **Step 6: 实现 `RunnerMetricsStore.__init__` 与 `asyncio.Lock`**

实现 `max_age=24h`、`now_fn` 注入、`_data` 字典桶、`_lock`。

- [ ] **Step 7: 实现 `append()` 与 `_prune_bucket()`**

在锁内写入点并剪枝。

- [ ] **Step 8: 实现 `get_latest()` 与 `get_series()`**

在锁内读取并按窗口过滤。

- [ ] **Step 8.1: 确保查询路径先剪枝再返回结果**

要求：`get_latest()` 和 `get_series()` 都在读取前执行 `_prune_bucket()`。

- [ ] **Step 9: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/unit/test_runner_metrics_store.py -v`
Expected: PASS。

- [ ] **Step 10: Commit**

```bash
git add backend/app/services/runner_metrics_store.py backend/tests/unit/test_runner_metrics_store.py
git commit -m "feat(backend): add in-memory runner metrics store"
```

### Task 2: 扩展 runner control API（metrics + overview + metrics query）

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_runner_control_api.py`

- [ ] **Step 1: 写失败测试 - metrics ingest 成功**

新增测试：`POST /api/runners/{runner_id}/metrics` 返回 200。

- [ ] **Step 2: 写失败测试 - metrics ingest 鉴权失败**

新增测试：错误 token 返回 403。

- [ ] **Step 2.1: 写失败测试 - metrics ingest 省略 timestamp 使用服务端时间**

新增测试：不传 `timestamp` 仍可写入，query 时 `series` 非空。

- [ ] **Step 3: 写失败测试 - 百分比越界返回 422**

新增测试：`cpu_percent=120` 返回 422。

- [ ] **Step 4: 写失败测试 - active_tasks<0 返回 422**

新增测试：`active_tasks=-1` 返回 422。

- [ ] **Step 5: 写失败测试 - invalid timestamp fallback**

新增测试：`timestamp="not-a-datetime"` 仍可写入，后续 query 可读。

- [ ] **Step 6: 写失败测试 - overview 返回 health + latest**

新增测试：`/api/admin/runners/overview` 含 `health_status` 与 `latest_metrics`。

- [ ] **Step 6.1: 写失败测试 - overview 无效 admin token 返回 403**

新增测试：`GET /api/admin/runners/overview` 使用错误 `X-Admin-Token` 返回 403。

- [ ] **Step 7: 写失败测试 - health 仅 online/offline/disabled**

新增测试：断言无 `idle`，并覆盖：
- disabled 优先返回 `disabled`
- 过阈值心跳返回 `offline`
- 否则返回 `online`

- [ ] **Step 8: 写失败测试 - metrics query 1h/6h/24h 均可用**

新增测试：`window=1h|6h|24h` 返回 200。

- [ ] **Step 9: 写失败测试 - metrics query 默认 window=1h**

新增测试：省略 window 时返回 `window == "1h"`。

- [ ] **Step 10: 写失败测试 - metrics query invalid window 返回 422**

新增测试：`window=2h` 返回 422。

- [ ] **Step 11: 写失败测试 - metrics query 空数据返回空 series**

新增测试：无点时返回 `series=[]` 且 `latest=null`。

- [ ] **Step 11.1: 写失败测试 - metrics query 无效 admin token 返回 403**

新增测试：`GET /api/admin/runners/{runner_id}/metrics` 使用错误 `X-Admin-Token` 返回 403。

- [ ] **Step 12: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -k "metrics or overview or window or health" -v`
Expected: FAIL（接口不存在）。

- [ ] **Step 13: 实现请求模型与响应结构**

在 `backend/app/main.py` 添加 `RunnerMetricsRequest` 与 metrics/overview query response 序列化结构。

- [ ] **Step 14: 实现 metrics ingest endpoint**

实现 `/api/runners/{runner_id}/metrics`：
- runner token 鉴权
- 范围校验（0-100）
- `active_tasks >= 0`
- timestamp 有效则使用，否则 fallback `datetime.now()`
- 写入内存 store

- [ ] **Step 15: 实现 overview endpoint**

实现 `/api/admin/runners/overview`：
- admin token 鉴权
- 返回 `runner_id/enabled/last_seen_at/health_status/latest_metrics`

- [ ] **Step 16: 实现 metrics query endpoint**

实现 `/api/admin/runners/{runner_id}/metrics`：
- 支持窗口 `1h/6h/24h`
- 非法 window 返回 422
- 返回 `runner/window/latest/series`
- `runner` 至少包含：`runner_id`、`enabled`、`last_seen_at`、`health_status`
- `series` 按时间升序

- [ ] **Step 17: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/integration/test_runner_control_api.py -v`
Expected: PASS。

- [ ] **Step 18: Commit**

```bash
git add backend/app/main.py backend/tests/integration/test_runner_control_api.py
git commit -m "feat(backend): add runner metrics ingest and admin query APIs"
```

### Task 3: 任务 payload 增加 `runner_id`（HTTP + WS）

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_api.py`
- Modify: `backend/tests/integration/test_websocket.py`

- [ ] **Step 1: 写失败测试 - `GET /api/tasks` 包含 runner_id**

新增测试断言任务列表项含 `runner_id`。

- [ ] **Step 2: 写失败测试 - `GET /api/tasks/{id}` 包含 runner_id**

新增测试断言详情含 `runner_id`。

- [ ] **Step 3: 写失败测试 - `GET /api/queue` 包含 runner_id**

新增测试断言 running/pending 项含 `runner_id`。

- [ ] **Step 4: 写失败测试 - task WS 初始 payload 包含 runner_id**

新增测试断言 websocket 首包含 `runner_id`。

- [ ] **Step 5: 写失败测试 - task WS 更新 payload 包含 runner_id**

新增测试通过 runner event 推动更新包并断言 `runner_id`。

- [ ] **Step 5.1: 写失败测试 - dashboard WS 的 `task_created/task_completed` 事件 payload 包含 runner_id**

新增测试：连接 `/ws/dashboard`，触发创建任务与完成事件，断言对应事件 payload 含 `runner_id`。

- [ ] **Step 6: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/integration/test_api.py tests/integration/test_websocket.py -k "runner_id" -v`
Expected: FAIL。

- [ ] **Step 7: 在 `TaskDetailResponse` 增加 `runner_id: Optional[str]`**

修改 `backend/app/main.py` response model。

- [ ] **Step 8: 在 `_task_to_response` 填充 runner_id**

填充 `runner_id=task.runner_id`。

- [ ] **Step 9: 在 `_task_to_dict` 填充 runner_id**

确保 queue 与 websocket payload 都含 `runner_id`。

- [ ] **Step 10: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/integration/test_api.py tests/integration/test_websocket.py -k "runner_id" -v`
Expected: PASS。

- [ ] **Step 11: Commit**

```bash
git add backend/app/main.py backend/tests/integration/test_api.py backend/tests/integration/test_websocket.py
git commit -m "feat(backend): include runner_id in task API and websocket payloads"
```

### Task 4: Runner client/worker 增加 metrics 上报（TDD）

**Files:**
- Modify: `backend/app/runner/client.py`
- Modify: `backend/app/runner/config.py`
- Modify: `backend/app/runner/worker.py`
- Modify: `backend/tests/unit/test_runner_client.py`
- Modify: `backend/tests/unit/test_runner_worker.py`
- Modify: `backend/tests/unit/test_config.py`

- [ ] **Step 1: 写失败测试 - client 发送 metrics 到正确路径**

新增测试断言路径：`/api/runners/{runner_id}/metrics`。

- [ ] **Step 2: 写失败测试 - worker run_once 上报 metrics**

新增测试断言一次循环至少上报一次。

- [ ] **Step 3: 写失败测试 - metrics 上报失败不影响主循环**

新增测试断言 `send_metrics` 抛错时 `run_once` 不崩溃。

- [ ] **Step 4: 写失败测试 - config 默认 interval=10s**

新增测试断言默认值 10.0。

- [ ] **Step 5: 写失败测试 - 环境变量覆盖 interval**

新增测试断言 `RUNNER_METRICS_INTERVAL_SECONDS=5` 被正确读取。

- [ ] **Step 6: 写失败测试 - run_forever 使用 interval 节奏**

新增测试通过 monkeypatch `asyncio.sleep` 验证使用配置 interval。

- [ ] **Step 7: 运行测试确认失败**

Run: `cd backend && uv run pytest tests/unit/test_runner_client.py tests/unit/test_runner_worker.py tests/unit/test_config.py -k "metrics or interval" -v`
Expected: FAIL。

- [ ] **Step 8: 在 `RunnerControlClient` 添加 `send_metrics()`**

修改 `backend/app/runner/client.py`。

- [ ] **Step 9: 在 `RunnerConfig` 添加 `metrics_interval_seconds`**

修改 `backend/app/runner/config.py` 并读取环境变量。

- [ ] **Step 10: 在 `RunnerWorker` 实现采集与上报**

修改 `backend/app/runner/worker.py`：
- 采集 `cpu/memory/disk/active_tasks`
- 调用 `send_metrics`
- 失败记录 warning 并继续

- [ ] **Step 11: 运行测试确认通过**

Run: `cd backend && uv run pytest tests/unit/test_runner_client.py tests/unit/test_runner_worker.py tests/unit/test_config.py -k "metrics or interval" -v`
Expected: PASS。

- [ ] **Step 12: Commit**

```bash
git add backend/app/runner/client.py backend/app/runner/config.py backend/app/runner/worker.py backend/tests/unit/test_runner_client.py backend/tests/unit/test_runner_worker.py backend/tests/unit/test_config.py
git commit -m "feat(runner): report metrics to control plane every interval"
```

### Task 5: Backend 回归与质量闸门

**Files:**
- Modify: none

- [ ] **Step 1: 运行目标后端测试集**

Run: `cd backend && uv run pytest tests/unit/test_runner_metrics_store.py tests/unit/test_runner_client.py tests/unit/test_runner_worker.py tests/integration/test_runner_control_api.py tests/integration/test_api.py tests/integration/test_websocket.py -v`
Expected: PASS。

- [ ] **Step 2: 验证无数据库变更（memory-only）**

Run: `git diff --name-only $(git merge-base HEAD master)..HEAD`
Expected: 输出中不包含 `backend/app/database.py`、`backend/migrations/`、`*.sql` 等 DB schema 相关文件路径。

- [ ] **Step 3: Commit（若仅测试无改动则跳过）**

Run: `git status --short`
Expected: 无新增改动则不提交。

---

## Chunk 2: Frontend integration + UI delivery

### Task 6: 新增 Runner badge 组件并接入任务页面

**Files:**
- Create: `frontend/src/components/RunnerIdBadge.vue`
- Modify: `frontend/src/views/TaskQueue.vue`
- Modify: `frontend/src/views/TaskList.vue`
- Modify: `frontend/src/views/TaskDetail.vue`

- [ ] **Step 1: 建立前端构建基线**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 2: 新建 `RunnerIdBadge.vue`**

实现：有值显示 badge，无值显示 `-`。

- [ ] **Step 3: 在 `TaskQueue.vue` 替换 runner 单元格为 badge**

渲染模式：`<RunnerIdBadge :runner-id="task.runner_id || ''" />`。

- [ ] **Step 4: 在 `TaskList.vue` 新增 runner 列并渲染 badge**

要求：列宽固定最小宽度，避免滚动时抖动。

- [ ] **Step 5: 在 `TaskDetail.vue` Details 区渲染 runner badge**

仅显示 ID，不显示 online/offline 文本。

- [ ] **Step 6: 显式检查“仅 badge 展示”约束**

Run: `cd frontend && rg "online|offline|disabled" src/views/TaskQueue.vue src/views/TaskList.vue src/views/TaskDetail.vue`
Expected: 不存在 runner 状态文本渲染（允许其他无关注释/字符串需人工确认）。

- [ ] **Step 7: 运行构建验证**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/RunnerIdBadge.vue frontend/src/views/TaskQueue.vue frontend/src/views/TaskList.vue frontend/src/views/TaskDetail.vue
git commit -m "feat(frontend): render runner_id as badge in task pages"
```

### Task 7: Dashboard 切换到 RunnerMonitorPanel

**Files:**
- Create: `frontend/src/components/RunnerMonitorPanel.vue`
- Modify: `frontend/src/services/api.js`
- Modify: `frontend/src/views/Dashboard.vue`

- [ ] **Step 1: 在 `api.js` 增加 `getRunnerOverview()`**

请求：`GET /admin/runners/overview`。

- [ ] **Step 2: 在 `api.js` 增加 `getRunnerMetrics(runnerId, window)`**

请求：`GET /admin/runners/{runnerId}/metrics?window=`。

- [ ] **Step 3: 新建 `RunnerMonitorPanel.vue` 基础骨架**

props：`runners/loading/error`。

- [ ] **Step 4: 在 `RunnerMonitorPanel.vue` 渲染 health 三态 badge**

仅 `online/offline/disabled`。

- [ ] **Step 5: 在 `RunnerMonitorPanel.vue` 渲染 latest 四项指标**

`cpu/memory/disk/active_tasks`，空值显示 `--`。

- [ ] **Step 6: 在 `Dashboard.vue` 删除 `SystemMonitor` import**

确保无未使用 import。

- [ ] **Step 7: 在 `Dashboard.vue` 删除 SystemMonitor 模板块**

替换为 `RunnerMonitorPanel`。

- [ ] **Step 8: 在 `Dashboard.vue` 增加 `runnerOverview` 状态**

包含 `data/loading/error`。

- [ ] **Step 9: 在数据刷新逻辑中调用 `api.getRunnerOverview()`**

与 dashboard task stats 同步刷新。

- [ ] **Step 9.1: 显式验证 admin token 会附带到 overview 请求**

检查 `api.js` 现有拦截器对 `/admin/*` 自动注入 `X-Admin-Token`，并确认 Dashboard 使用的是 `api.js` 中 `baseURL='/api'` 下的 `/admin/runners/overview`（实际请求路径为 `/api/admin/runners/overview`）。

- [ ] **Step 10: 将刷新间隔固定为 10 秒**

与采样频率一致。

- [ ] **Step 10.1: 增加 Dashboard 上的 403 错误态文案**

当 overview 请求 403 时，提示 admin token 无效或缺失，复用现有 admin token 错误风格。

- [ ] **Step 11: 调整 Dashboard 小屏布局为单列**

375px 下无横向滚动。

- [ ] **Step 12: 运行构建验证**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 13: 手动验证 375px/768px/1024px 布局**

在三个视口下确认 runner 卡片布局符合预期。

- [ ] **Step 14: Commit**

```bash
git add frontend/src/services/api.js frontend/src/components/RunnerMonitorPanel.vue frontend/src/views/Dashboard.vue
git commit -m "feat(frontend): replace system monitor with runner overview panel"
```

### Task 8: Runner 页面增加详情与曲线（1h/6h/24h）

**Files:**
- Create: `frontend/src/components/RunnerMetricsChart.vue`
- Modify: `frontend/src/views/RunnerList.vue`

- [ ] **Step 1: 新建 `RunnerMetricsChart.vue`（SVG 折线）**

输入：`points/field/maxY`。

- [ ] **Step 2: 在 `RunnerList.vue` 增加详情状态（selected/window/loading/error/data）**

默认 `window='1h'`。

- [ ] **Step 3: 增加“点击 runner 行展开详情”行为**

可切换当前选中 runner。

- [ ] **Step 4: 增加窗口切换控件（1h/6h/24h）**

切换后重新拉取 metrics。

- [ ] **Step 5: 增加 10 秒自动刷新当前选中 runner 详情**

离开页面时清理定时器。

- [ ] **Step 5.1: 显式验证 admin token 会附带到 runner metrics 请求**

确认 `api.getRunnerMetrics()` 走 `api.js` 中 `baseURL='/api'` 下的 `/admin/runners/{id}/metrics`（实际请求路径为 `/api/admin/runners/{id}/metrics`），并由拦截器注入 `X-Admin-Token`。

- [ ] **Step 6: 在详情区渲染基础信息**

`runner_id`、`enabled`、`last_seen_at`、`health_status badge`。

- [ ] **Step 7: 在详情区渲染 CPU 曲线卡**

使用 `RunnerMetricsChart`。

- [ ] **Step 8: 在详情区渲染 Memory 曲线卡**

使用 `RunnerMetricsChart`。

- [ ] **Step 9: 在详情区渲染 Disk 曲线卡**

使用 `RunnerMetricsChart`。

- [ ] **Step 10: 在详情区渲染 Active Tasks 曲线卡**

`maxY` 动态：`Math.max(1, series max)`。

- [ ] **Step 11: 增加空态和错误态 UI**

无数据显示“暂无监控数据”，查询失败显示错误文案。

- [ ] **Step 11.1: 增加 Runner 页面 403 错误态文案**

当 metrics 请求返回 403 时，提示 admin token 无效或缺失。

- [ ] **Step 12: 调整移动端布局（详情与图表单列）**

375px 下无横向溢出。

- [ ] **Step 13: 运行构建验证**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 14: 手动验证 375px/768px/1024px 视口**

验证窗口切换、曲线刷新、详情布局。

- [ ] **Step 15: Commit**

```bash
git add frontend/src/components/RunnerMetricsChart.vue frontend/src/views/RunnerList.vue
git commit -m "feat(frontend): add runner detail panel with metrics curves"
```

### Task 9: 端到端验收与收尾

**Files:**
- Modify: optional fixups from verification

- [ ] **Step 1: 后端目标测试回归**

Run: `cd backend && uv run pytest tests/unit/test_runner_metrics_store.py tests/unit/test_runner_client.py tests/unit/test_runner_worker.py tests/integration/test_runner_control_api.py tests/integration/test_api.py tests/integration/test_websocket.py -v`
Expected: PASS。

- [ ] **Step 2: 前端构建回归**

Run: `cd frontend && npm run build`
Expected: PASS。

- [ ] **Step 3: 手动验收矩阵**

- Dashboard 已替换为 Runner 监控面板。
- Queue/TaskList/TaskDetail 仅显示 runner_id badge。
- Runner 页面显示基础详情 + 4 曲线 + 1h/6h/24h。
- 健康状态仅有 `online/offline/disabled`。
- 指标为空时展示空态，不报错。
- 375px/768px/1024px 均无明显布局问题。

- [ ] **Step 4: 若有修复文件，先确认变更列表**

Run: `git status --short`
Expected: 列出待提交修复文件。

- [ ] **Step 5: 提交收尾修复（仅在 Step 4 有文件时执行）**

```bash
git add <具体文件列表>
git commit -m "fix: polish runner monitoring UI integration"
```

---

## 执行注意事项

- 严格按 `TDD`：先写失败测试，再实现最小代码，再跑通过。
- 仅实现 spec 范围内能力，避免扩展到 DB 持久化或额外状态枚举。
- 保持任务页面“仅 badge 展示 runner_id”的约束，不引入状态文本。
- window 非法值后端统一返回 422，前端展示错误状态。
- metrics 存储仅内存实现，本计划禁止新增 DB schema/migration。

## Self-Review Checklist

1. spec 要求全覆盖：
   - dashboard runner 监控替换 system monitor
   - task 页面 runner_id badge
   - runner 页面详情 + 曲线与窗口切换
   - metrics 内存缓存不落库
   - 健康状态仅 `online/offline/disabled`
2. 新 API 与前端调用契约一致：
   - `/api/runners/{runner_id}/metrics`
   - `/api/admin/runners/overview`
   - `/api/admin/runners/{runner_id}/metrics?window=`
3. 后端 contract 覆盖：
   - `GET /api/tasks` / `GET /api/tasks/{id}` / `GET /api/queue` / task websocket payload 均包含 `runner_id`
4. 回归命令可直接执行，且覆盖 backend + frontend 关键路径。
