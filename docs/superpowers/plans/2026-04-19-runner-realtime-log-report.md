# Runner 实时日志与 POC/Case 汇报 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 runner 改为在任务执行期间增量上报日志 chunk 和 progress event（含 case/poc 计数），并修复后端终态事件处理的通用性 bug。

**Architecture:** 新增 `TaskReporter` 组件与 `docker.run()` 并发执行，每 3 秒增量 flush 日志、每 10 秒扫描 testgen 目录发送 progress event；后端将非 `started`/`progress` 的所有 event_type 统一视为终态。

**Tech Stack:** Python 3.11+, FastAPI, pytest, asyncio, SQLite

---

## 文件变更映射

| 文件 | 变更 | 职责 |
|------|------|------|
| `backend/runner/reporter.py` | 新增 | `TaskReporter`：增量日志上报 + progress event 发送 |
| `backend/runner/executor.py` | 修改 | 集成 `TaskReporter`，移除 `_upload_logs` |
| `backend/app/database.py` | 修改 | `apply_task_event` 终态事件通用化 |
| `backend/app/main.py` | 修改 | `ingest_runner_task_event` 终态通用化 + progress counts 处理 |
| `backend/tests/unit/runner/test_reporter.py` | 新增 | `TaskReporter` 单元测试 |
| `backend/tests/unit/test_database.py` | 修改 | 新增 `apply_task_event` 终态测试 |
| `backend/tests/unit/test_main.py` | 修改 | 新增 progress counts + 终态通用化 API 测试 |
| `backend/tests/unit/runner/test_executor.py` | 修改 | 移除 `_upload_logs` 测试，适配 `TaskReporter` |

---

## Task 1: 修复 database.py —— 终态事件通用化

**Files:**
- Modify: `backend/app/database.py:186-204`
- Test: `backend/tests/unit/test_database.py`

- [ ] **Step 1: 写测试 —— 验证 started/progress 保持 running，其余 event_type 设为终态**

在 `backend/tests/unit/test_database.py` 末尾追加：

```python
def test_apply_task_event_started_sets_running(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    result = db.apply_task_event(task_id, 1, "started")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.started_at is not None
    assert task.finished_at is None


def test_apply_task_event_progress_sets_running(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "progress")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.RUNNING
    assert task.finished_at is None


def test_apply_task_event_completed_sets_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "completed")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.finished_at is not None


def test_apply_task_event_failed_sets_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "failed")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert task.finished_at is not None


def test_apply_task_event_unknown_type_treats_as_terminal(db):
    task_id = db.create_task("serde", "1.0.0", "/path", "/log1", "/log2")
    db.apply_task_event(task_id, 1, "started")
    result = db.apply_task_event(task_id, 2, "timeout")
    assert result is True
    task = db.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert task.finished_at is not None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/unit/test_database.py::test_apply_task_event_unknown_type_treats_as_terminal -v
```

Expected: FAIL — `timeout` event does not update status/finished_at

- [ ] **Step 3: 修改 `backend/app/database.py`**

找到 `apply_task_event` 方法中的事件处理分支（约第 186-204 行），将：

```python
        if event_type in ("started", "progress"):
            updates.append("status = ?")
            params.append(TaskStatus.RUNNING.value)
            updates.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        elif event_type == "completed":
            updates.append("status = ?")
            params.append(TaskStatus.COMPLETED.value)
            updates.append("finished_at = ?")
            params.append(now)
        elif event_type == "failed":
            updates.append("status = ?")
            params.append(TaskStatus.FAILED.value)
            updates.append("finished_at = ?")
            params.append(now)
```

替换为：

```python
        if event_type in ("started", "progress"):
            updates.append("status = ?")
            params.append(TaskStatus.RUNNING.value)
            updates.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        else:
            # All non-running events are terminal
            if event_type == "completed":
                terminal_status = TaskStatus.COMPLETED.value
            else:
                terminal_status = TaskStatus.FAILED.value
            updates.append("status = ?")
            params.append(terminal_status)
            updates.append("finished_at = ?")
            params.append(now)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/unit/test_database.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/database.py tests/unit/test_database.py
cd backend && git commit -m "fix(database): treat all non-running event types as terminal states

All event types other than 'started' and 'progress' now update
status to a terminal state (COMPLETED for 'completed', FAILED
for everything else) and set finished_at.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 2: 修复 main.py —— 终态通用化 + progress counts

**Files:**
- Modify: `backend/app/main.py:755-784`
- Test: `backend/tests/unit/test_main.py`

- [ ] **Step 1: 写测试 —— progress event 更新 counts，未知终态事件正确设 failed**

在 `backend/tests/unit/test_main.py` 末尾追加：

```python
def test_progress_event_updates_counts_and_broadcasts(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-progress")
    task_id = create_pending_task(client, monkeypatch, crate_name="progress-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/events",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "event_seq": 2,
            "event_type": "progress",
            "case_count": 5,
            "poc_count": 2,
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] is True

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["case_count"] == 5
    assert task_data["poc_count"] == 2
    assert task_data["status"] == "running"


def test_terminal_event_type_generic_treats_unknown_as_failed(client, monkeypatch):
    runner_id, token = create_runner_and_token(client, "runner-terminal-generic")
    task_id = create_pending_task(client, monkeypatch, crate_name="generic-crate")
    claimed_task_id, lease_token = claim_task(client, runner_id, token)
    assert claimed_task_id == task_id

    client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/events",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "event_seq": 1,
            "event_type": "started",
        },
    )

    response = client.post(
        f"/api/runners/{runner_id}/tasks/{task_id}/events",
        headers=auth_headers(token),
        json={
            "lease_token": lease_token,
            "event_seq": 2,
            "event_type": "timeout",
            "exit_code": -1,
            "message": "Execution timed out",
        },
    )

    assert response.status_code == 200
    assert response.json()["applied"] is True

    task_response = client.get(f"/api/tasks/{task_id}")
    assert task_response.status_code == 200
    task_data = task_response.json()
    assert task_data["status"] == "failed"
    assert task_data["finished_at"] is not None
    assert task_data["message"] == "Execution timed out"
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/unit/test_main.py::test_progress_event_updates_counts_and_broadcasts tests/unit/test_main.py::test_terminal_event_type_generic_treats_unknown_as_failed -v
```

Expected: FAIL — progress event does not update counts; `timeout` event is not handled as terminal

- [ ] **Step 3: 修改 `backend/app/main.py`**

找到 `ingest_runner_task_event` 方法（约第 715 行），修改以下内容：

**A. 终态事件通用化（约第 755-769 行）**

将：
```python
        if applied and request.event_type in ("completed", "failed"):
            db.update_task_status(
                task_id,
                TaskStatus.COMPLETED if request.event_type == "completed" else TaskStatus.FAILED,
                exit_code=request.exit_code,
                message=request.message,
            )
            if request.case_count is not None or request.poc_count is not None:
                db.update_task_counts(
                    task_id,
                    case_count=request.case_count,
                    poc_count=request.poc_count,
                )
            if request.compile_failed is not None:
                db.update_task_compile_failed(task_id, request.compile_failed)
```

替换为：
```python
        if applied and request.event_type not in ("started", "progress"):
            terminal_status = (
                TaskStatus.COMPLETED
                if request.event_type == "completed"
                else TaskStatus.FAILED
            )
            db.update_task_status(
                task_id,
                terminal_status,
                exit_code=request.exit_code,
                message=request.message,
            )
            if request.case_count is not None or request.poc_count is not None:
                db.update_task_counts(
                    task_id,
                    case_count=request.case_count,
                    poc_count=request.poc_count,
                )
            if request.compile_failed is not None:
                db.update_task_compile_failed(task_id, request.compile_failed)
```

**B. progress event counts 处理（在终态处理代码块之后、WebSocket 广播之前插入）**

在终态处理代码块之后，添加：
```python
        if applied and request.event_type == "progress":
            if request.case_count is not None or request.poc_count is not None:
                db.update_task_counts(
                    task_id,
                    case_count=request.case_count,
                    poc_count=request.poc_count,
                )
```

**C. WebSocket 广播的 dashboard type 通用化**

将：
```python
                dashboard_payload["type"] = (
                    "task_completed"
                    if request.event_type in ("completed", "failed")
                    else "task_update"
                )
```

替换为：
```python
                dashboard_payload["type"] = (
                    "task_completed"
                    if request.event_type not in ("started", "progress")
                    else "task_update"
                )
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/unit/test_main.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add app/main.py tests/unit/test_main.py
cd backend && git commit -m "feat(api): handle progress event counts and generic terminal events

- progress events now update case_count/poc_count in DB
- all event types other than started/progress trigger terminal
  status update and task_completed dashboard broadcast

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 3: 新增 TaskReporter 组件

**Files:**
- Create: `backend/runner/reporter.py`
- Test: `backend/tests/unit/runner/test_reporter.py`

- [ ] **Step 1: 写测试**

创建 `backend/tests/unit/runner/test_reporter.py`：

```python
import asyncio
import pytest
from pathlib import Path
from runner.reporter import TaskReporter


@pytest.mark.asyncio
async def test_reporter_flush_logs_sends_incremental_chunks(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append((task_id, log_type, payload))

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
    )

    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0][1] == "stdout"
    assert sent_chunks[0][2]["chunk_seq"] == 1
    assert sent_chunks[0][2]["content"] == "line 1\n"
    assert sent_chunks[0][2]["lease_token"] == "lease-1"

    with open(log_file, "a") as f:
        f.write("line 2\n")

    sent_chunks.clear()
    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0][2]["content"] == "line 2\n"
    assert sent_chunks[0][2]["chunk_seq"] == 2


@pytest.mark.asyncio
async def test_reporter_flush_logs_skips_unchanged_file(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    class FakeClient:
        async def send_log_chunk(self, *_args, **_kwargs):
            raise AssertionError("should not be called")

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
    )

    await reporter._flush_logs()
    await reporter._flush_logs()


@pytest.mark.asyncio
async def test_reporter_flush_logs_retries_on_failure(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("content")

    call_count = 0

    class FailingClient:
        async def send_log_chunk(self, *_args, **_kwargs):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("network error")

    reporter = TaskReporter(
        client=FailingClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
    )

    await reporter._flush_logs()
    assert call_count == 1

    await reporter._flush_logs()
    assert call_count == 2


@pytest.mark.asyncio
async def test_reporter_flush_logs_handles_truncation(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("old content here")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
    )

    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0]["content"] == "old content here"

    # Truncate file to smaller content
    log_file.write_text("new")

    sent_chunks.clear()
    await reporter._flush_logs()
    assert len(sent_chunks) == 1
    assert sent_chunks[0]["content"] == "new"
    assert sent_chunks[0]["chunk_seq"] == 2


@pytest.mark.asyncio
async def test_reporter_progress_only_sends_when_counts_change(tmp_path):
    (tmp_path / "testgen" / "tests" / "a").mkdir(parents=True)
    (tmp_path / "testgen" / "poc" / "x").mkdir(parents=True)

    sent_events = []

    class FakeClient:
        async def send_event(self, task_id, payload):
            sent_events.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    reporter._last_progress_time = 0
    await reporter._maybe_send_progress()
    assert len(sent_events) == 1
    assert sent_events[0]["event_type"] == "progress"
    assert sent_events[0]["case_count"] == 1
    assert sent_events[0]["poc_count"] == 1

    reporter._last_progress_time = 0
    sent_events.clear()
    await reporter._maybe_send_progress()
    assert len(sent_events) == 0


@pytest.mark.asyncio
async def test_reporter_progress_respects_interval(tmp_path):
    (tmp_path / "testgen" / "tests" / "a").mkdir(parents=True)

    sent_events = []

    class FakeClient:
        async def send_event(self, task_id, payload):
            sent_events.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    # Set last_progress_time to now to simulate recent send
    reporter._last_progress_time = asyncio.get_running_loop().time()
    await reporter._maybe_send_progress()
    assert len(sent_events) == 0


@pytest.mark.asyncio
async def test_reporter_stop_returns_incrementing_seq(tmp_path):
    reporter = TaskReporter(
        client=type("C", (), {"send_log_chunk": lambda *a, **k: None})(),
        task_id=1,
        lease_token="lease-1",
        log_paths={},
        workspace_dir=tmp_path,
    )

    seq1 = reporter.stop()
    seq2 = reporter.stop()
    assert seq1 == 2  # started uses 1
    assert seq2 == 3
    assert seq2 > seq1


@pytest.mark.asyncio
async def test_reporter_run_loop_stops_on_event(tmp_path):
    log_file = tmp_path / "stdout.log"
    log_file.write_text("line 1\n")

    sent_chunks = []

    class FakeClient:
        async def send_log_chunk(self, task_id, log_type, payload):
            sent_chunks.append(payload)

    reporter = TaskReporter(
        client=FakeClient(),
        task_id=1,
        lease_token="lease-1",
        log_paths={"stdout": log_file},
        workspace_dir=tmp_path,
    )

    run_task = asyncio.create_task(reporter.run())
    await asyncio.sleep(0.1)
    reporter.stop()
    await run_task

    assert len(sent_chunks) >= 1
    assert sent_chunks[0]["content"] == "line 1\n"


def test_reporter_count_generated_items(tmp_path):
    testgen = tmp_path / "testgen"
    (testgen / "tests" / "a").mkdir(parents=True)
    (testgen / "tests" / "b").mkdir(parents=True)
    (testgen / "poc" / "x").mkdir(parents=True)
    (testgen / "poc" / "y").mkdir(parents=True)
    (testgen / "poc" / "z").mkdir(parents=True)

    reporter = object.__new__(TaskReporter)
    reporter.workspace_dir = tmp_path

    assert reporter._count_generated_items() == (2, 3)
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/unit/runner/test_reporter.py -v
```

Expected: 全部 FAIL — `TaskReporter` class does not exist

- [ ] **Step 3: 实现 `backend/runner/reporter.py`**

创建 `backend/runner/reporter.py`：

```python
import asyncio
import logging
from pathlib import Path
from typing import Tuple

from runner.client import RunnerControlClient

logger = logging.getLogger(__name__)


class TaskReporter:
    LOG_FLUSH_INTERVAL = 3.0
    PROGRESS_INTERVAL = 10.0

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
        self.log_paths = log_paths
        self.workspace_dir = workspace_dir
        self._stop_event = asyncio.Event()
        self._next_chunk_seq: dict[str, int] = {}
        self._sent_offsets: dict[str, int] = {}
        self._next_event_seq = 2  # started uses 1
        self._last_counts: Tuple[int, int] = (0, 0)
        self._last_progress_time = 0.0

    async def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                await self._flush_logs()
                await self._maybe_send_progress()

                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self.LOG_FLUSH_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass
        finally:
            await self._flush_logs()

    def stop(self) -> int:
        self._stop_event.set()
        seq = self._next_event_seq
        self._next_event_seq += 1
        return seq

    async def _flush_logs(self) -> None:
        for log_type, path in self.log_paths.items():
            if not path.exists():
                continue

            try:
                current_size = path.stat().st_size
            except OSError:
                continue

            sent_offset = self._sent_offsets.get(log_type, 0)
            chunk_seq = self._next_chunk_seq.get(log_type, 1)

            if current_size == sent_offset:
                continue

            if current_size < sent_offset:
                sent_offset = 0

            try:
                with open(path, "rb") as f:
                    f.seek(sent_offset)
                    new_bytes = f.read()
            except OSError:
                continue

            if not new_bytes:
                continue

            new_content = new_bytes.decode("utf-8", errors="replace")

            try:
                await self.client.send_log_chunk(
                    self.task_id,
                    log_type,
                    {
                        "lease_token": self.lease_token,
                        "chunk_seq": chunk_seq,
                        "content": new_content,
                    },
                )
                self._sent_offsets[log_type] = current_size
                self._next_chunk_seq[log_type] = chunk_seq + 1
            except Exception as exc:
                logger.warning(
                    "log chunk send failed: %s",
                    exc,
                    extra={
                        "task_id": self.task_id,
                        "log_type": log_type,
                        "chunk_seq": chunk_seq,
                    },
                )

    async def _maybe_send_progress(self) -> None:
        now = asyncio.get_running_loop().time()
        if now - self._last_progress_time < self.PROGRESS_INTERVAL:
            return

        case_count, poc_count = self._count_generated_items()
        if (case_count, poc_count) == self._last_counts:
            return

        self._last_counts = (case_count, poc_count)
        self._last_progress_time = now

        event_seq = self._next_event_seq
        self._next_event_seq += 1

        try:
            await self.client.send_event(
                self.task_id,
                {
                    "lease_token": self.lease_token,
                    "event_seq": event_seq,
                    "event_type": "progress",
                    "case_count": case_count,
                    "poc_count": poc_count,
                },
            )
        except Exception as exc:
            logger.warning(
                "progress event send failed: %s",
                exc,
                extra={
                    "task_id": self.task_id,
                    "event_seq": event_seq,
                },
            )

    def _count_generated_items(self) -> Tuple[int, int]:
        testgen_dir = self.workspace_dir / "testgen"
        case_count = 0
        poc_count = 0

        tests_dir = testgen_dir / "tests"
        if tests_dir.exists():
            case_count = len([d for d in tests_dir.iterdir() if d.is_dir()])

        poc_dir = testgen_dir / "poc"
        if poc_dir.exists():
            poc_count = len([d for d in poc_dir.iterdir() if d.is_dir()])

        return case_count, poc_count
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/unit/runner/test_reporter.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add runner/reporter.py tests/unit/runner/test_reporter.py
cd backend && git commit -m "feat(runner): add TaskReporter for incremental log and progress reporting

TaskReporter runs concurrently with docker execution:
- Flushes log increments every 3s via chunk API
- Sends progress events every 10s when case/poc counts change
- Performs final flush on stop() before terminal event

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 4: 改造 executor.py —— 集成 TaskReporter

**Files:**
- Modify: `backend/runner/executor.py`
- Test: `backend/tests/unit/runner/test_executor.py`

- [ ] **Step 1: 更新 executor 测试**

修改 `backend/tests/unit/runner/test_executor.py`：

**A. 删除两个 `_upload_logs` 测试**

删除 `test_upload_logs_sends_all_log_types_with_chunk_seq`（第 31-66 行）和 `test_executor_upload_logs_include_decisions`（第 141-168 行）两个函数。

**B. 修改 `test_execute_claimed_task_does_not_block_event_loop`**

找到第 106 行：
```python
    executor._upload_logs = noop_upload_logs
```
删除这一行（`TaskReporter` 会处理日志发送，无需 mock `_upload_logs`）。

**C. 修改 `test_executor_logs_lifecycle_boundaries`**

在测试开头添加 import：
```python
from runner.reporter import TaskReporter
```

在 `executor = TaskExecutor(...)` 之前添加 mock：
```python
    class FakeReporter:
        def __init__(self, *args, **kwargs):
            pass
        async def run(self):
            pass
        def stop(self):
            return 2

    monkeypatch.setattr("runner.executor.TaskReporter", FakeReporter)
```

**D. 修改 `test_executor_failure_logs_traceback`**

同样在测试中添加相同的 `FakeReporter` mock。

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend && uv run pytest tests/unit/runner/test_executor.py -v
```

Expected: 部分 FAIL — `TaskReporter` not imported in executor; `_upload_logs` still referenced

- [ ] **Step 3: 修改 `backend/runner/executor.py`**

**A. 在文件顶部添加 import**

在现有 import 之后添加：
```python
from runner.reporter import TaskReporter
```

**B. 重写 `execute_claimed_task` 方法**

将 `execute_claimed_task` 方法的 try 块重写为：

```python
    async def execute_claimed_task(self, claimed: dict) -> None:
        task_id = claimed["id"]
        lease_token = claimed["lease_token"]
        crate_name = claimed["crate_name"]
        crate_version = claimed["version"]

        await self.client.send_event(
            task_id,
            {"lease_token": lease_token, "event_seq": 1, "event_type": "started"},
        )

        workspace_dir = (
            Path(self.config.workspace_dir) / "repos" / f"{crate_name}-{crate_version}"
        )
        logs_dir = Path(self.config.workspace_dir) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        runner_log = logs_dir / f"{task_id}-runner.log"
        stdout_log = logs_dir / f"{task_id}-stdout.log"
        stderr_log = logs_dir / f"{task_id}-stderr.log"

        handler = logging.FileHandler(str(runner_log), mode="w")
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        task_logger = logging.getLogger(f"task.{task_id}")
        task_logger.setLevel(logging.DEBUG)
        task_logger.handlers.clear()
        task_logger.addHandler(handler)

        task_ctx = {
            "task_id": task_id,
            "crate_name": crate_name,
            "version": crate_version,
        }

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
            task_logger.info("task started", extra=task_ctx)

            if not await asyncio.to_thread(self.docker.is_available):
                raise RuntimeError("Docker is not available")

            if not await asyncio.to_thread(
                self.docker.ensure_image, self.config.docker_pull_policy
            ):
                raise RuntimeError(
                    f"Docker image {self.config.docker_image} is not available"
                )

            await self._prepare_workspace(
                workspace_dir, crate_name, crate_version, task_logger
            )

            cmd = ["cargo", "rapx", f"--test-crate={crate_name}", "test"]
            task_logger.info(
                "command started",
                extra={**task_ctx, "command_summary": " ".join(cmd)},
            )

            result = await self.docker.run(
                command=cmd,
                workspace_dir=workspace_dir,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
            )
            task_logger.info(
                "command finished",
                extra={**task_ctx, "exit_code": result.exit_code},
            )

            case_count, poc_count = await asyncio.to_thread(
                self._count_generated_items, workspace_dir
            )
            compile_failed = await asyncio.to_thread(
                self._get_compile_failed_count, workspace_dir
            )

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
            task_logger.info(
                "task terminal event sent",
                extra={**task_ctx, "terminal_status": result.state.value},
            )
        except asyncio.CancelledError:
            task_logger.info("task cancelled", extra=task_ctx)
            terminal_seq = reporter.stop()
            await reporter_task
            await self.client.send_event(
                task_id,
                {
                    "lease_token": lease_token,
                    "event_seq": terminal_seq,
                    "event_type": "failed",
                    "message": "Task interrupted by shutdown",
                },
            )
            raise
        except Exception as exc:
            task_logger.exception("task execution failed", extra=task_ctx)
            terminal_seq = reporter.stop()
            await reporter_task
            await self.client.send_event(
                task_id,
                {
                    "lease_token": lease_token,
                    "event_seq": terminal_seq,
                    "event_type": "failed",
                    "message": str(exc),
                },
            )
        finally:
            task_logger.info("task runner log closed", extra=task_ctx)
            task_logger.removeHandler(handler)
            handler.close()
```

**C. 删除 `_upload_logs` 方法**

删除 `_upload_logs` 方法的完整定义（第 208-250 行左右）。

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend && uv run pytest tests/unit/runner/test_executor.py -v
```

Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
cd backend && git add runner/executor.py tests/unit/runner/test_executor.py
cd backend && git commit -m "feat(runner): integrate TaskReporter into executor

- TaskReporter runs concurrently with docker execution
- Removes _upload_logs; all logs are sent incrementally by reporter
- Terminal event uses seq from reporter.stop() to ensure ordering

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Task 5: 全量回归测试与代码格式化

- [ ] **Step 1: 运行全部测试**

```bash
cd backend && uv run pytest -v
```

Expected: 全部 PASS。如有失败，先修复再提交。

- [ ] **Step 2: 格式化代码**

```bash
cd backend && uv run black app/ tests/ runner/
```

- [ ] **Step 3: 如有格式变更，提交**

```bash
cd backend && git diff --quiet || git commit -am "style: format code with black"
```

---

## Spec Coverage Self-Review

| Spec 要求 | 对应 Task |
|-----------|-----------|
| TaskReporter 增量日志上报（3s 间隔） | Task 3 |
| TaskReporter progress event（10s 间隔，计数变化时） | Task 3 |
| TaskReporter stop() 返回 seq | Task 3 |
| TaskReporter final flush | Task 3 |
| executor 集成 TaskReporter，并发运行 | Task 4 |
| executor 移除 `_upload_logs` | Task 4 |
| terminal event 使用 reporter 的 seq | Task 4 |
| 后端 progress event 更新 counts | Task 2 |
| 后端 progress event 广播 WebSocket | Task 2 |
| 后端终态事件通用化 | Task 1 + Task 2 |
| 文件截断处理 | Task 3 (test_reporter_flush_logs_handles_truncation) |
| chunk 发送失败重试 | Task 3 (test_reporter_flush_logs_retries_on_failure) |

**无遗漏。**

## Placeholder Scan

- 无 "TBD"/"TODO"/"implement later"
- 所有测试代码完整
- 所有实现代码完整
- 无 "Similar to Task N" 引用

## Type Consistency

- `TaskReporter.stop()` 返回 `int` — 所有调用点一致
- `event_seq` 类型为 `int` — 前后一致
- `chunk_seq` 类型为 `int` — 前后一致
- `log_paths` 类型为 `dict[str, Path]` — 前后一致
