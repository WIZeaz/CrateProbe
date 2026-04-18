# Runner 实时日志与 POC/Case 汇报设计

## 背景

当前 runner 在任务执行期间不向后端汇报任何中间状态，所有日志和 POC/case 计数只在任务完成后通过 `_upload_logs` 和 terminal event 一次性上报。这导致前端在任务执行过程中无法查看实时日志，也无法看到 POC/case 的生成进度。

本设计将 runner 改为**增量实时汇报**：
- 日志内容：增量 chunk 发送，3 秒间隔
- POC/case 计数：通过 progress event 发送，10 秒间隔（仅当计数变化时）

## 目标

- 任务执行期间，前端可以实时查看最新日志（LogViewer 自动刷新机制）
- 任务执行期间，前端可以看到 POC/case 计数的增长
- 不改动前端代码，利用现有 WebSocket + 自动刷新机制
- 任务完成后无需额外的 `_upload_logs`，所有内容已由 reporter 发送

## 非目标

- 前端流式显示（如 `tail -f` 效果）—— 保持现有 LogViewer 轮询机制
- WebSocket 推送日志内容 —— 日志仍通过 HTTP API 获取
- 硬实时保证 —— 3 秒/10 秒间隔是尽力而为

## 架构

```
┌─────────────────────────────────────────────────────────────┐
│  TaskExecutor.execute_claimed_task                          │
│                                                             │
│  ┌──────────────────────┐    ┌──────────────────────────┐  │
│  │ docker.run()         │    │ TaskReporter.run()       │  │
│  │ (原有逻辑)           │    │ (新增 background task)   │  │
│  │                      │    │                          │  │
│  │ 生成 stdout/stderr   │    │ 每 3s: 增量 chunk 发送   │  │
│  │ 生成 testgen/        │    │ 每 10s: progress event   │  │
│  │ 生成 runner.log      │    │                          │  │
│  └──────────────────────┘    └──────────────────────────┘  │
│           │                             │                   │
│           └────────── 并发执行 ───────────┘                   │
│                                                             │
│  docker 完成后:                                             │
│    1. reporter.stop() → 获取 terminal event seq            │
│    2. await reporter_task (final flush)                    │
│    3. 发送 terminal event (completed/failed)               │
└─────────────────────────────────────────────────────────────┘
```

## 组件设计

### TaskReporter

新增文件：`backend/runner/reporter.py`

职责：
1. 增量日志上报：为每个 log_type 维护 `sent_offset` 和 `next_chunk_seq`，定期读取文件新增内容并发送 chunk
2. 增量计数上报：定期扫描 `testgen/` 目录，计数变化时发送 progress event
3. 优雅停止：停止后执行 final flush，确保所有内容已发送
4. 事件序列号管理：为 progress event 和 terminal event 分配递增的 `event_seq`

```python
class TaskReporter:
    def __init__(
        self,
        client: RunnerControlClient,
        task_id: int,
        lease_token: str,
        log_paths: dict[str, Path],
        workspace_dir: Path,
    ):
        self.client = client
        self.task_id = task_id
        self.lease_token = lease_token
        self.log_paths = log_paths  # log_type -> Path
        self.workspace_dir = workspace_dir
        self._stop_event = asyncio.Event()
        self._next_chunk_seq: dict[str, int] = {}  # log_type -> next chunk seq
        self._sent_offsets: dict[str, int] = {}    # log_type -> bytes sent
        self._next_event_seq = 2                   # started uses 1
        self._last_counts = (0, 0)
        self._last_progress_time = 0.0
```

核心方法：

- `async run()`: 主循环，每 3 秒调用 `_flush_logs()` 和 `_maybe_send_progress()`，停止后执行 final flush
- `stop() -> int`: 设置停止事件，返回当前可用的 `event_seq`（供 terminal event 使用）
- `async _flush_logs()`: 遍历所有 log_type，读取文件新增内容，发送 chunk。发送成功才更新 offset 和 chunk_seq；失败则下次重试
- `async _maybe_send_progress()`: 每 10 秒执行一次，扫描 testgen 目录，计数变化时发送 progress event
- `_count_generated_items() -> Tuple[int, int]`: 扫描 `testgen/tests` 和 `testgen/poc` 目录，返回 case_count 和 poc_count

### TaskExecutor 改造

修改 `backend/runner/executor.py`：

1. 导入 `TaskReporter`
2. 在 `execute_claimed_task` 中，启动 docker run 的同时启动 `TaskReporter`
3. docker run 完成后，调用 `reporter.stop()` 获取 terminal event seq，等待 reporter final flush
4. 发送 terminal event 时使用 reporter 返回的 seq
5. 移除 `_upload_logs` 方法，所有日志由 reporter 处理

关键代码片段：

```python
reporter = TaskReporter(
    client=self.client,
    task_id=task_id,
    lease_token=lease_token,
    log_paths={
        "stdout": stdout_log,
        "stderr": stderr_log,
        "runner": runner_log,
        "miri_report": workspace_dir / "testgen" / "miri_report.txt",
        "stats-yaml": workspace_dir / "testgen" / "stats.yaml",
    },
    workspace_dir=workspace_dir,
)
reporter_task = asyncio.create_task(reporter.run())

try:
    result = await self.docker.run(...)
    # ... 统计 case_count, poc_count, compile_failed ...
finally:
    terminal_seq = reporter.stop()
    await reporter_task

await self.client.send_event(
    task_id,
    {
        "lease_token": lease_token,
        "event_seq": terminal_seq,
        "event_type": result.state.value,
        "exit_code": result.exit_code,
        "message": result.message,
        "case_count": case_count,
        "poc_count": poc_count,
        "compile_failed": compile_failed,
    },
)
```

### 后端 API 改动

修改 `backend/app/main.py`：`ingest_runner_task_event` 对 `progress` 事件也处理 counts：

```python
if applied and request.event_type == "progress":
    if request.case_count is not None or request.poc_count is not None:
        db.update_task_counts(
            task_id,
            case_count=request.case_count,
            poc_count=request.poc_count,
        )
    updated_task = db.get_task(task_id)
    if updated_task is not None:
        task_payload = _task_to_dict(updated_task)
        task_payload["type"] = "task_update"
        await ws_manager.broadcast_task_update(task_id, task_payload)
        dashboard_payload = _task_to_dict(updated_task)
        dashboard_payload["type"] = "task_update"
        await ws_manager.broadcast_dashboard_update(dashboard_payload)
```

`apply_task_event` 对 `progress` 事件已有处理（更新 status=running），无需改动。

### 前端

无需改动。现有机制：
- `TaskDetail.vue` 监听 WebSocket `task_update`，自动刷新任务元数据（含 case_count/poc_count）
- `LogViewer` 组件自动轮询日志 API，获取最新内容

## 关键设计决策

### 1. chunk_seq 和 event_seq 的分配策略

- **chunk_seq**: 按 log_type 独立维护。发送成功后才递增，失败则保持原值，下次重试。确保后端 `record_task_log_chunk` 的防重放机制正常工作。
- **event_seq**: 全局递增（所有 progress 事件共享一个序列）。发送前预分配，发送失败时该 seq 被"消耗"但无害（后端只拒绝 <= last_event_seq 的 seq，跳过的 seq 不影响后续事件）。terminal event 的 seq 从 `stop()` 获取，确保大于所有已发送的 progress seq。

### 2. 为什么移除 `_upload_logs`

TaskReporter 在 final flush 阶段会发送所有 log_type 的剩余内容。任务正常结束时，所有日志已由 reporter 发送完毕，无需额外的 `_upload_logs`。

异常场景（如 reporter 发送失败多次）：final flush 会在 docker 完成后立即执行，此时网络通常已恢复；如果仍然失败，chunk 内容会在 runner 本地日志文件中保留，可通过其他方式排查。

### 3. 为什么 reporter 同时处理 runner log

runner log（`{task_id}-runner.log`）由 `task_logger` 写入，记录了任务执行的关键节点（下载、解压、命令启动/完成等）。实时上报 runner log 有助于后端了解任务执行阶段。

### 4. miri_report 和 stats-yaml 的处理

这两个文件在任务执行过程中可能生成，也可能不存在。reporter 的处理逻辑：
- 文件不存在：跳过
- 文件存在：按 offset 增量发送
- 任务过程中文件从无到有：首次检查时文件大小 > 0，发送完整内容

### 5. 并发安全性

TaskReporter 的所有状态（`_next_chunk_seq`、`_sent_offsets`、`_next_event_seq`）只在 asyncio 单线程事件循环中修改，无需额外锁。`stop()` 在主任务中同步调用，返回 seq 给 terminal event 使用，不存在竞争条件。

## 错误处理

| 场景 | 行为 |
|------|------|
| chunk 发送失败 | 记录 warning，不更新 offset/chunk_seq，下次重试 |
| progress event 发送失败 | 记录 warning，seq 已消耗但无害，下次用新 seq |
| reporter task 异常 | `execute_claimed_task` 的 try/finally 确保 reporter 停止，docker run 不受影响 |
| 任务被取消 | finally 块中停止 reporter 并发送 terminal failed event |
| 文件在读取时被截断 | 如果当前 size < sent_offset，重置 offset 为 0，发送完整文件 |

## 测试策略

### 单元测试

- `TaskReporter._flush_logs`: 验证 offset 和 chunk_seq 的正确计算，发送失败时的重试行为
- `TaskReporter._maybe_send_progress`: 验证仅在计数变化时发送，10 秒间隔控制
- `TaskReporter.stop()`: 验证返回的 seq 大于所有已发送的 progress seq

### 集成测试

- 模拟长时间运行的任务，验证 progress 事件和 log chunks 被正确接收并写入数据库/文件
- 验证 terminal event 的 seq 大于所有 progress event seq
- 验证任务取消时 reporter 正确停止，terminal failed event 正常发送

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|---------|------|
| `backend/runner/reporter.py` | 新增 | TaskReporter 组件 |
| `backend/runner/executor.py` | 修改 | 集成 TaskReporter，移除 `_upload_logs` |
| `backend/app/main.py` | 修改 | progress event 处理 counts 和 WebSocket 广播 |
| `backend/tests/unit/test_reporter.py` | 新增 | TaskReporter 单元测试 |
| `backend/tests/integration/test_api.py` | 修改 | 新增 progress event 集成测试 |
