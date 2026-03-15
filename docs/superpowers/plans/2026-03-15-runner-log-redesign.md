# Runner Log + Log Viewer Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add per-task runner log to TaskExecutor, generalize the log API endpoint, and redesign LogViewer to a left-right split layout.

**Architecture:** Backend writes a `{task_id}-runner.log` file during task execution using a per-task Python logger. Two generic log API endpoints replace the six existing ones, resolving paths via a `LOG_PATH_RESOLVERS` dict. Frontend `LogViewer.vue` replaces horizontal tabs with a vertical file list (left) + content area (right).

**Tech Stack:** Python/FastAPI (backend), Vue 3 Composition API + Tailwind CSS v4 (frontend), pytest (tests), uv (Python package manager)

**Working directory for all commands:** `/home/wizeaz/exp-plat/.worktrees/runner-log-redesign`

---

## Chunk 1: Backend — Runner Log in TaskExecutor

**Files:**
- Modify: `backend/app/services/task_executor.py`
- Modify: `backend/tests/unit/test_task_executor.py`

### Task 1: Add runner log writing to `execute_task`

- [ ] **Step 1: Write failing tests for runner log behavior**

Add to `backend/tests/unit/test_task_executor.py`:

```python
import logging
from pathlib import Path


@pytest.mark.asyncio
async def test_execute_task_creates_runner_log(executor, db, config, tmp_path):
    """Runner log file is created when a task executes"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stderr.log"),
    )

    with patch.object(executor, "prepare_workspace", new_callable=AsyncMock) as mock_prep:
        mock_prep.return_value = config.workspace_path / "repos" / "test-crate-1.0.0"
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 12345
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

    runner_log = config.workspace_path / "logs" / f"{task_id}-runner.log"
    assert runner_log.exists(), "Runner log file must be created"
    content = runner_log.read_text()
    assert "started" in content.lower()


@pytest.mark.asyncio
async def test_execute_task_runner_log_uses_task_id(executor, db, config):
    """Runner log path uses task ID, not crate name"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "serde",
        "1.0.0",
        str(config.workspace_path / "repos" / "serde-1.0.0"),
        str(config.workspace_path / "logs" / "serde-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "serde-1.0.0-stderr.log"),
    )

    with patch.object(executor, "prepare_workspace", new_callable=AsyncMock) as mock_prep:
        mock_prep.return_value = config.workspace_path / "repos" / "serde-1.0.0"
        with patch("asyncio.create_subprocess_exec") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.pid = 99
            mock_process.wait.return_value = 0
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            await executor.execute_task(task_id)

    # Must use task_id (integer), NOT crate name
    expected_path = config.workspace_path / "logs" / f"{task_id}-runner.log"
    bad_path = config.workspace_path / "logs" / "serde-1.0.0-runner.log"
    assert expected_path.exists()
    assert not bad_path.exists()


@pytest.mark.asyncio
async def test_execute_task_runner_log_records_exception(executor, db, config):
    """Runner log captures exceptions that cause task failure"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "bad-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "bad-crate-1.0.0"),
        str(config.workspace_path / "logs" / "bad-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "bad-crate-1.0.0-stderr.log"),
    )

    with patch.object(
        executor, "prepare_workspace", new_callable=AsyncMock,
        side_effect=RuntimeError("Download failed: connection refused")
    ):
        await executor.execute_task(task_id)

    runner_log = config.workspace_path / "logs" / f"{task_id}-runner.log"
    assert runner_log.exists()
    content = runner_log.read_text()
    assert "Download failed" in content or "ERROR" in content


@pytest.mark.asyncio
async def test_runner_logger_named_by_task_id(executor, db, config):
    """Runner logger is named f'task.{task_id}' to avoid collisions"""
    config.workspace_path.mkdir(parents=True, exist_ok=True)
    (config.workspace_path / "logs").mkdir(parents=True, exist_ok=True)

    task_id = db.create_task(
        "test-crate",
        "1.0.0",
        str(config.workspace_path / "repos" / "test-crate-1.0.0"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stdout.log"),
        str(config.workspace_path / "logs" / "test-crate-1.0.0-stderr.log"),
    )

    captured_logger_names = []

    original_get_logger = logging.getLogger

    def spy_get_logger(name=None):
        if name and name.startswith("task."):
            captured_logger_names.append(name)
        return original_get_logger(name)

    with patch("logging.getLogger", side_effect=spy_get_logger):
        with patch.object(executor, "prepare_workspace", new_callable=AsyncMock) as mock_prep:
            mock_prep.return_value = config.workspace_path / "repos" / "test-crate-1.0.0"
            with patch("asyncio.create_subprocess_exec") as mock_subprocess:
                mock_process = AsyncMock()
                mock_process.pid = 1
                mock_process.wait.return_value = 0
                mock_process.returncode = 0
                mock_subprocess.return_value = mock_process
                await executor.execute_task(task_id)

    assert f"task.{task_id}" in captured_logger_names
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/unit/test_task_executor.py::test_execute_task_creates_runner_log tests/unit/test_task_executor.py::test_execute_task_runner_log_uses_task_id tests/unit/test_task_executor.py::test_execute_task_runner_log_records_exception tests/unit/test_task_executor.py::test_runner_logger_named_by_task_id -v 2>&1 | tail -20
```

Expected: 4 FAILED (runner log file not created)

- [ ] **Step 3: Implement runner logging in `execute_task`**

In `backend/app/services/task_executor.py`, modify `execute_task` to add runner logging. Add this at the very top of the method, before anything else:

```python
async def execute_task(self, task_id: int):
    """Execute a single task"""
    import logging

    task = self.db.get_task(task_id)
    if not task:
        return

    # Set up per-task runner logger — first action, before status update or any branch
    runner_log_path = (
        self.config.workspace_path / "logs" / f"{task_id}-runner.log"
    )
    runner_log_path.parent.mkdir(parents=True, exist_ok=True)
    task_logger = logging.getLogger(f"task.{task_id}")
    task_logger.setLevel(logging.DEBUG)
    # Remove existing handlers to avoid duplicates on retry
    task_logger.handlers.clear()
    handler = logging.FileHandler(str(runner_log_path), mode="w")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    )
    task_logger.addHandler(handler)

    try:
        task_logger.info(
            f"Task #{task_id} started: {task.crate_name} {task.version} "
            f"(mode={self.execution_mode})"
        )

        # Update status to running
        self.db.update_task_status(
            task_id, TaskStatus.RUNNING, started_at=datetime.now()
        )

        # Prepare workspace
        task_logger.info(f"Downloading crate {task.crate_name} {task.version}...")
        try:
            workspace_dir = await self.prepare_workspace(
                task_id, task.crate_name, task.version
            )
            task_logger.info(f"Workspace ready: {workspace_dir}")
        except Exception as e:
            task_logger.error(f"Workspace preparation failed: {e}")
            raise

        # Ensure log directory exists
        Path(task.stdout_log).parent.mkdir(parents=True, exist_ok=True)
        Path(task.stderr_log).parent.mkdir(parents=True, exist_ok=True)

        if self.execution_mode == "docker":
            task_logger.info("Checking Docker availability...")
            if not self.docker_runner.is_available():
                msg = "Docker is not available but execution_mode is 'docker'"
                task_logger.error(msg)
                raise RuntimeError(msg)

            task_logger.info(
                f"Ensuring Docker image: {self.config.docker_image} "
                f"(policy={self.config.docker_pull_policy})"
            )
            if not self.docker_runner.ensure_image(self.config.docker_pull_policy):
                msg = f"Docker image {self.config.docker_image} is not available"
                task_logger.error(msg)
                raise RuntimeError(msg)

            cmd = [
                "cargo",
                "rapx",
                "-testgen",
                f"-test-crate={task.crate_name}",
            ]
            task_logger.info(f"Running command: {' '.join(cmd)}")

            exit_code = await self.docker_runner.run(
                command=cmd,
                workspace_dir=workspace_dir,
                stdout_log=Path(task.stdout_log),
                stderr_log=Path(task.stderr_log),
            )
            task_logger.info(f"Process exited with code: {exit_code}")

            # Final count of generated items
            case_count, poc_count = self.count_generated_items(workspace_dir)
            self.db.update_task_counts(task_id, case_count, poc_count)

            # Update final status
            if exit_code == 0:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.COMPLETED,
                    finished_at=datetime.now(),
                    exit_code=exit_code,
                )
            elif exit_code == 137:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.OOM,
                    finished_at=datetime.now(),
                    exit_code=exit_code,
                )
            elif exit_code == -1:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.TIMEOUT,
                    finished_at=datetime.now(),
                    exit_code=exit_code,
                )
            else:
                self.db.update_task_status(
                    task_id,
                    TaskStatus.FAILED,
                    finished_at=datetime.now(),
                    exit_code=exit_code,
                )
        else:
            # Use traditional execution with systemd/resource
            await self._execute_with_limiter(task_id, workspace_dir, task)

    except Exception as e:
        task_logger.error(f"Task failed with exception: {e}")
        self.db.update_task_status(
            task_id,
            TaskStatus.FAILED,
            finished_at=datetime.now(),
            error_message=str(e),
        )
    finally:
        task_logger.info(f"Task #{task_id} runner log closed.")
        task_logger.removeHandler(handler)
        handler.close()
```

Also update `_execute_with_limiter` to log via the task logger. Replace the start of `_execute_with_limiter`:

```python
async def _execute_with_limiter(self, task_id: int, workspace_dir: Path, task):
    """Execute task using systemd/resource limiter (original implementation)"""
    import logging
    task_logger = logging.getLogger(f"task.{task_id}")

    # Build command
    cmd = self.limiter.build_command(
        ["cargo", "rapx", "-testgen", f"-test-crate={task.crate_name}"],
        cwd=str(workspace_dir),
    )
    task_logger.info(f"Running command: {' '.join(cmd)}")

    # Open log files
    stdout_log = open(task.stdout_log, "w")
    stderr_log = open(task.stderr_log, "w")

    # Start process
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=stdout_log,
        stderr=stderr_log,
        cwd=workspace_dir,
        preexec_fn=(
            self.limiter.apply_resource_limits
            if self.limiter.get_limit_method().value == "resource"
            else None
        ),
    )
    task_logger.info(f"Process started with PID: {process.pid}")

    # Store PID
    self.db.update_task_pid(task_id, process.pid)

    # Wait for completion with periodic stats updates
    await self._wait_with_stats_updates(process, task_id, workspace_dir)

    stdout_log.close()
    stderr_log.close()

    task_logger.info(f"Process exited with code: {process.returncode}")

    # Final count of generated items
    case_count, poc_count = self.count_generated_items(workspace_dir)
    self.db.update_task_counts(task_id, case_count, poc_count)

    # Update final status based on exit code
    if process.returncode == 0:
        self.db.update_task_status(
            task_id,
            TaskStatus.COMPLETED,
            finished_at=datetime.now(),
            exit_code=process.returncode,
        )
    elif process.returncode in (-9, 137):
        self.db.update_task_status(
            task_id,
            TaskStatus.OOM,
            finished_at=datetime.now(),
            exit_code=process.returncode,
        )
    elif process.returncode in (-24, -14):
        self.db.update_task_status(
            task_id,
            TaskStatus.TIMEOUT,
            finished_at=datetime.now(),
            exit_code=process.returncode,
        )
    else:
        self.db.update_task_status(
            task_id,
            TaskStatus.FAILED,
            finished_at=datetime.now(),
            exit_code=process.returncode,
        )
```

- [ ] **Step 4: Run the new tests**

```bash
cd backend && uv run pytest tests/unit/test_task_executor.py::test_execute_task_creates_runner_log tests/unit/test_task_executor.py::test_execute_task_runner_log_uses_task_id tests/unit/test_task_executor.py::test_execute_task_runner_log_records_exception tests/unit/test_task_executor.py::test_runner_logger_named_by_task_id -v 2>&1 | tail -20
```

Expected: 4 PASSED

- [ ] **Step 5: Run full unit test suite**

```bash
cd backend && uv run pytest tests/unit/ -v 2>&1 | tail -20
```

Expected: all tests pass (including existing executor tests)

- [ ] **Step 6: Format and commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/services/task_executor.py backend/tests/unit/test_task_executor.py
git commit -m "feat: add per-task runner log to TaskExecutor"
```

---

## Chunk 2: Backend — Generalized Log API Endpoint

**Files:**
- Modify: `backend/app/main.py`
- Modify: `backend/tests/integration/test_api.py`

### Task 2: Replace 6 log endpoints with 2 generic ones

- [ ] **Step 1: Write failing tests for the generic log endpoint**

Add to `backend/tests/integration/test_api.py`:

```python
def test_generic_log_endpoint_stdout(client, config):
    """GET /api/tasks/{id}/logs/stdout returns last N lines"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    log_file = config.workspace_path / "logs" / "serde-1.0.0-stdout.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.write_text("\n".join([f"line {i}" for i in range(1, 6)]))

    response = client.get(f"/api/tasks/{task_id}/logs/stdout?lines=3")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert len(data["lines"]) == 3
    assert data["lines"][-1] == "line 5"


def test_generic_log_endpoint_runner(client, config):
    """GET /api/tasks/{id}/logs/runner returns runner log content"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    runner_log = config.workspace_path / "logs" / f"{task_id}-runner.log"
    runner_log.parent.mkdir(parents=True, exist_ok=True)
    runner_log.write_text("[INFO] Task started\n[INFO] Downloading...\n[INFO] Done")

    response = client.get(f"/api/tasks/{task_id}/logs/runner")

    assert response.status_code == 200
    data = response.json()
    assert "lines" in data
    assert any("Task started" in line for line in data["lines"])


def test_generic_log_endpoint_unknown_name(client):
    """GET /api/tasks/{id}/logs/{unknown} returns 404 Unknown log type"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/logs/unknown_log_type")

    assert response.status_code == 404
    assert "Unknown log type" in response.json()["detail"]


def test_generic_log_raw_unknown_name(client):
    """GET /api/tasks/{id}/logs/{unknown}/raw returns 404 Unknown log type"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/logs/unknown_log_type/raw")

    assert response.status_code == 404
    assert "Unknown log type" in response.json()["detail"]


def test_generic_log_raw_endpoint(client, config):
    """GET /api/tasks/{id}/logs/{name}/raw returns full plain text content"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    log_file = config.workspace_path / "logs" / "serde-1.0.0-stdout.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_content = "Full content line 1\nFull content line 2"
    log_file.write_text(log_content)

    response = client.get(f"/api/tasks/{task_id}/logs/stdout/raw")

    assert response.status_code == 200
    assert log_content in response.text


def test_generic_log_endpoint_file_missing(client):
    """GET /api/tasks/{id}/logs/{name} returns 404 when file doesn't exist"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/logs/stdout")

    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_log_path_resolvers_all_types(config):
    """LOG_PATH_RESOLVERS produces correct paths for all 4 known log types"""
    from app.main import LOG_PATH_RESOLVERS

    # Simulate a task record
    task = type("Task", (), {
        "id": 7,
        "crate_name": "serde",
        "version": "1.0.0",
        "stdout_log": str(config.workspace_path / "logs" / "serde-1.0.0-stdout.log"),
        "stderr_log": str(config.workspace_path / "logs" / "serde-1.0.0-stderr.log"),
        "workspace_path": str(config.workspace_path / "repos" / "serde-1.0.0"),
    })()

    stdout_path = LOG_PATH_RESOLVERS["stdout"](task, config)
    assert stdout_path == Path(task.stdout_log)

    stderr_path = LOG_PATH_RESOLVERS["stderr"](task, config)
    assert stderr_path == Path(task.stderr_log)

    runner_path = LOG_PATH_RESOLVERS["runner"](task, config)
    assert runner_path == config.workspace_path / "logs" / "7-runner.log"

    miri_path = LOG_PATH_RESOLVERS["miri_report"](task, config)
    assert miri_path == Path(task.workspace_path) / "testgen" / "miri_report.txt"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd backend && uv run pytest tests/integration/test_api.py::test_generic_log_endpoint_stdout tests/integration/test_api.py::test_generic_log_endpoint_runner tests/integration/test_api.py::test_generic_log_endpoint_unknown_name tests/integration/test_api.py::test_generic_log_raw_unknown_name tests/integration/test_api.py::test_generic_log_raw_endpoint tests/integration/test_api.py::test_generic_log_endpoint_file_missing tests/integration/test_api.py::test_log_path_resolvers_all_types -v 2>&1 | tail -20
```

Expected: all FAILED (endpoints don't exist yet)

- [ ] **Step 3: Implement generic endpoints in `main.py`**

In `backend/app/main.py`, add `LOG_PATH_RESOLVERS` at module level (after imports, before `create_app`):

```python
LOG_PATH_RESOLVERS = {
    "stdout": lambda task, _cfg: Path(task.stdout_log),
    "stderr": lambda task, _cfg: Path(task.stderr_log),
    "runner": lambda task, cfg: cfg.workspace_path
    / "logs"
    / f"{task.id}-runner.log",
    "miri_report": lambda task, _cfg: Path(task.workspace_path)
    / "testgen"
    / "miri_report.txt",
}
```

Then, inside `create_app`:

**a) Delete the following 6 functions** (find them by their decorator + function name and remove them entirely):
- `async def get_stdout_logs(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/stdout")`
- `async def get_stderr_logs(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/stderr")`
- `async def get_miri_report_logs(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/miri_report")`
- `async def download_stdout_raw(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/stdout/raw", ...)`
- `async def download_stderr_raw(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/stderr/raw", ...)`
- `async def download_miri_report_raw(...)` with decorator `@app.get("/api/tasks/{task_id}/logs/miri_report/raw", ...)`

**b) Add the following 2 generic endpoints** in their place:

```python
@app.get("/api/tasks/{task_id}/logs/{log_name}")
async def get_task_log(
    task_id: int, log_name: str, lines: int = Query(default=1000, ge=0)
):
    """Get last N lines of any task log by name"""
    if log_name not in LOG_PATH_RESOLVERS:
        raise HTTPException(status_code=404, detail="Unknown log type")

    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    log_path = LOG_PATH_RESOLVERS[log_name](task, config)

    try:
        log_lines = read_last_n_lines(str(log_path), lines)
        return {"lines": log_lines}
    except CustomFileNotFoundError:
        raise HTTPException(status_code=404, detail="Log file not found")


@app.get(
    "/api/tasks/{task_id}/logs/{log_name}/raw",
    response_class=PlainTextResponse,
)
async def get_task_log_raw(task_id: int, log_name: str):
    """Download full content of any task log by name"""
    if log_name not in LOG_PATH_RESOLVERS:
        raise HTTPException(status_code=404, detail="Unknown log type")

    task = db.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    log_path = LOG_PATH_RESOLVERS[log_name](task, config)

    if not log_path.exists():
        raise HTTPException(status_code=404, detail="Log file not found")

    return PlainTextResponse(
        log_path.read_text(encoding="utf-8", errors="replace")
    )
```

- [ ] **Step 4: Verify existing tests still pass with new generic endpoints**

The following existing tests in `backend/tests/integration/test_api.py` use the same URL shape and will continue to pass without modification:

- `test_get_task_stdout_logs` — hits `/logs/stdout` → now served by generic handler ✓
- `test_get_task_stderr_logs` — hits `/logs/stderr` → now served by generic handler ✓
- `test_get_task_logs_not_found` — hits `/logs/stdout` on missing task → 404 Task not found ✓
- `test_get_task_logs_file_missing` — hits `/logs/stdout`, asserts `"not found" in detail.lower()` → "Log file not found" passes ✓
- `test_download_stdout_raw` — hits `/logs/stdout/raw` → now served by generic raw handler ✓

Also add one additional test to confirm the unified error message for miri_report (since the old endpoint returned `"Miri report file not found"`, now it returns `"Log file not found"`):

```python
def test_miri_report_file_missing_uses_unified_error(client):
    """miri_report file-not-found returns unified 'Log file not found' message"""
    create_resp = client.post(
        "/api/tasks", json={"crate_name": "serde", "version": "1.0.0"}
    )
    task_id = create_resp.json()["task_id"]

    response = client.get(f"/api/tasks/{task_id}/logs/miri_report")

    assert response.status_code == 404
    assert response.json()["detail"] == "Log file not found"
```

- [ ] **Step 5: Run new tests**

```bash
cd backend && uv run pytest tests/integration/test_api.py::test_generic_log_endpoint_stdout tests/integration/test_api.py::test_generic_log_endpoint_runner tests/integration/test_api.py::test_generic_log_endpoint_unknown_name tests/integration/test_api.py::test_generic_log_raw_unknown_name tests/integration/test_api.py::test_generic_log_raw_endpoint tests/integration/test_api.py::test_generic_log_endpoint_file_missing tests/integration/test_api.py::test_log_path_resolvers_all_types tests/integration/test_api.py::test_miri_report_file_missing_uses_unified_error -v 2>&1 | tail -20
```

Expected: 8 PASSED

- [ ] **Step 6: Run full test suite**

```bash
cd backend && uv run pytest -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 7: Format and commit**

```bash
cd backend && uv run black app/ tests/
git add backend/app/main.py backend/tests/integration/test_api.py
git commit -m "feat: generalize log API to /logs/{name} with LOG_PATH_RESOLVERS"
```

---

## Chunk 3: Frontend — LogViewer Left-Right Redesign

**Files:**
- Modify: `frontend/src/components/LogViewer.vue`

### Task 3: Rewrite LogViewer.vue to left-right layout

- [ ] **Step 1: Replace the entire `LogViewer.vue` with the new implementation**

Replace `frontend/src/components/LogViewer.vue` content with:

```vue
<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
import api from '../services/api'

const props = defineProps({
  taskId: [String, Number],
  autoScroll: {
    type: Boolean,
    default: true
  }
})

const activeLog = ref('runner')
const logs = ref({
  runner: '',
  stdout: '',
  stderr: '',
  miri_report: ''
})
const loading = ref({
  runner: false,
  stdout: false,
  stderr: false,
  miri_report: false
})
const logContainer = ref(null)
let refreshInterval = null

const logFiles = [
  { id: 'runner', label: 'runner', icon: '⚙' },
  { id: 'stdout', label: 'stdout', icon: '📄' },
  { id: 'stderr', label: 'stderr', icon: '📄' },
  { id: 'miri_report', label: 'miri_report', icon: '📄' },
]

async function loadLog(logType, isRefresh = false) {
  if (!isRefresh && loading.value[logType]) return

  if (!isRefresh) {
    loading.value[logType] = true
  }

  try {
    const data = await api.getLog(props.taskId, logType, 1000)

    let wasAtBottom = false
    if (isRefresh && logContainer.value) {
      const threshold = 50
      wasAtBottom =
        logContainer.value.scrollHeight -
          logContainer.value.scrollTop -
          logContainer.value.clientHeight <
        threshold
    }

    if (data.lines && Array.isArray(data.lines)) {
      logs.value[logType] = data.lines.join('\n') || 'No content available'
    } else {
      logs.value[logType] = data.content || 'No content available'
    }

    if ((!isRefresh && props.autoScroll) || (isRefresh && wasAtBottom)) {
      scrollToBottom()
    }
  } catch (err) {
    if (err.response?.status === 404) {
      logs.value[logType] = 'No content available'
    } else {
      logs.value[logType] = `Error loading log: ${err.response?.data?.detail || err.message}`
    }
  } finally {
    if (!isRefresh) {
      loading.value[logType] = false
    }
  }
}

function startAutoRefresh() {
  refreshInterval = setInterval(() => {
    loadLog(activeLog.value, true)
  }, 5000)
}

function stopAutoRefresh() {
  if (refreshInterval) {
    clearInterval(refreshInterval)
    refreshInterval = null
  }
}

async function downloadLog() {
  try {
    const blob = await api.downloadLog(props.taskId, activeLog.value)
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const ext = activeLog.value === 'miri_report' ? 'txt' : 'log'
    a.download = `task-${props.taskId}-${activeLog.value}.${ext}`
    document.body.appendChild(a)
    a.click()
    window.URL.revokeObjectURL(url)
    document.body.removeChild(a)
  } catch (err) {
    console.error('Failed to download log:', err)
    alert(`Failed to download log: ${err.response?.data?.detail || err.message}`)
  }
}

function scrollToBottom() {
  setTimeout(() => {
    if (logContainer.value) {
      logContainer.value.scrollTop = logContainer.value.scrollHeight
    }
  }, 100)
}

watch(activeLog, (newLog) => {
  if (!logs.value[newLog]) {
    loadLog(newLog)
  } else if (props.autoScroll) {
    scrollToBottom()
  }
})

onMounted(() => {
  loadLog(activeLog.value)
  startAutoRefresh()
})

onUnmounted(() => {
  stopAutoRefresh()
})
</script>

<template>
  <div class="bento-card">
    <div class="flex items-center justify-between mb-4">
      <h3 class="text-lg font-semibold text-gray-900">Logs</h3>
      <button
        @click="downloadLog"
        class="px-3 py-1.5 text-sm font-medium text-blue-600 bg-blue-50 rounded-lg hover:bg-blue-100 transition-colors"
        :title="`Download ${activeLog}`"
      >
        Download {{ activeLog }}
      </button>
    </div>

    <div class="flex" style="min-height: 400px;">
      <!-- Left: file list -->
      <div
        class="flex flex-col flex-shrink-0 border-r border-gray-200"
        style="width: 160px;"
      >
        <div class="text-xs font-semibold text-gray-500 uppercase tracking-wide px-3 py-2">
          Files
        </div>

        <div class="flex flex-col gap-1 px-2 flex-1">
          <button
            v-for="file in logFiles"
            :key="file.id"
            @click="activeLog = file.id"
            :class="[
              'flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium text-left transition-colors w-full',
              activeLog === file.id
                ? 'bg-blue-50 border border-blue-200 text-blue-700'
                : 'text-gray-600 hover:bg-gray-100'
            ]"
          >
            <span>{{ file.icon }}</span>
            <span class="truncate">{{ file.label }}</span>
          </button>
        </div>

        <div class="px-3 py-2 text-xs text-gray-400 border-t border-gray-100 mt-2">
          ↻ 5s refresh
        </div>
      </div>

      <!-- Right: log content -->
      <div class="flex-1 min-w-0">
        <div
          ref="logContainer"
          class="log-viewer h-full"
          style="max-height: 500px; overflow-y: auto;"
        >
          <div v-if="loading[activeLog]" class="flex justify-center py-8">
            <div class="spinner border-white"></div>
          </div>
          <pre v-else class="text-sm">{{ logs[activeLog] || 'No content available' }}</pre>
        </div>
      </div>
    </div>
  </div>
</template>
```

- [ ] **Step 2: Verify the frontend compiles without errors**

```bash
cd frontend && npm run build 2>&1 | tail -20
```

Expected: build succeeds with no errors

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/LogViewer.vue
git commit -m "feat: redesign LogViewer to left-right layout with runner log"
```

---

## Chunk 4: Final Verification

### Task 4: Run complete test suite and format check

- [ ] **Step 1: Run all backend tests**

```bash
cd backend && uv run pytest -v 2>&1 | tail -30
```

Expected: all tests pass

- [ ] **Step 2: Verify Python formatting**

```bash
cd backend && uv run black --check app/ tests/ 2>&1
```

Expected: "All done! ✨ 🍰 ✨" with no reformatting needed. If files need formatting, run `uv run black app/ tests/` then re-check.

- [ ] **Step 3: Run frontend build**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

Expected: build succeeds

- [ ] **Step 4: Final commit if any formatting fixes were needed**

Only if Step 2 required reformatting:

```bash
cd backend && uv run black app/ tests/
git add -u
git commit -m "style: format Python code with black"
```
