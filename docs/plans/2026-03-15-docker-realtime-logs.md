# Docker Real-Time Log Streaming Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Docker execution mode so that stdout/stderr log files are written in real-time during container execution, not just after the container exits.

**Architecture:** Replace the current `container.wait() → container.logs()` pattern with concurrent streaming using the Docker SDK's `logs(stream=True, follow=True)` generator, running each stream in a thread executor (since Docker SDK is synchronous) and collecting them with `asyncio.gather()`.

**Tech Stack:** Python asyncio, Docker SDK (`docker-py`), `asyncio.get_running_loop().run_in_executor()`

---

## Background

### The Bug

In `backend/app/utils/docker_runner.py`, the `run()` method currently:

1. Starts the container (line 115)
2. **Blocks** until the container exits: `container.wait(timeout=...)` (line 129)
3. Only *after* exit, fetches all logs at once: `container.logs(...)` (lines 137–141)
4. Writes the full log blob to files: `stdout_log.write_text(...)` (lines 145–146)

During the entire container lifetime, the log files on disk are **empty or missing**. The REST endpoints `/api/tasks/{id}/logs/stdout` and `/api/tasks/{id}/logs/stderr` (in `main.py`) read from those files via `read_last_n_lines()`, so they return nothing useful during Docker execution. The systemd/resource mode does not have this problem because it passes real file handles directly to `asyncio.create_subprocess_exec()`.

### The Fix

The Docker SDK supports `container.logs(stream=True, follow=True, stdout=True, stderr=False)` which returns a generator that yields bytes chunks as they are produced. We run two such generators concurrently (one for stdout, one for stderr) in thread executor tasks, each writing chunks to the respective log file as they arrive. We also run `container.wait()` (with timeout) concurrently. All three are gathered with `asyncio.gather()`.

### File Map

| File | Change |
|------|--------|
| `backend/app/utils/docker_runner.py` | Replace `wait()+logs()` with streaming; add `_stream_logs()` helper |
| `backend/tests/unit/test_docker_runner.py` | Update existing mock for `container.logs`, add new streaming tests |

No other files need to change. The `TaskExecutor` calls `docker_runner.run()` and only sees the return value (exit code); the contract is unchanged.

---

## Chunk 1: Core Streaming Implementation

### Task 1: Write failing tests for streaming behavior

**Files:**
- Modify: `backend/tests/unit/test_docker_runner.py`

- [ ] **Step 1: Read the existing test file to understand the mock structure**

  The file is at `backend/tests/unit/test_docker_runner.py`. The key existing test is `test_run_builds_correct_command`, which mocks `docker.from_env` and sets:
  ```python
  mock_container.wait.return_value = {"StatusCode": 0}
  mock_container.logs.return_value = b"test output"
  ```
  We need to update this mock AND add new tests for streaming behavior.

- [ ] **Step 2: Add new streaming tests to the test file**

  Append the following tests to `backend/tests/unit/test_docker_runner.py`:

  ```python
  @pytest.mark.asyncio
  async def test_logs_file_content_correct_after_streaming(docker_runner, tmp_path):
      """Log files contain the correct content after streaming completes."""
      stdout_log = tmp_path / "stdout.log"
      stderr_log = tmp_path / "stderr.log"
      workspace = tmp_path / "workspace"
      workspace.mkdir()

      # Simulate streaming: logs() returns an iterable of byte chunks
      stdout_chunks = [b"line1\n", b"line2\n"]
      stderr_chunks = [b"err1\n"]

      with patch("docker.from_env") as mock_docker:
          mock_client = Mock()
          mock_container = Mock()
          mock_container.wait.return_value = {"StatusCode": 0}

          # logs() is called twice: once for stdout (stderr=False), once for stderr (stdout=False)
          def logs_side_effect(*args, **kwargs):
              if kwargs.get("stderr") and not kwargs.get("stdout"):
                  return iter(stderr_chunks)
              return iter(stdout_chunks)

          mock_container.logs.side_effect = logs_side_effect
          mock_client.containers.run.return_value = mock_container
          mock_docker.return_value = mock_client

          exit_code = await docker_runner.run(
              command=["cargo", "rapx"],
              workspace_dir=workspace,
              stdout_log=stdout_log,
              stderr_log=stderr_log,
          )

          assert exit_code == 0
          assert stdout_log.read_text() == "line1\nline2\n"
          assert stderr_log.read_text() == "err1\n"


  @pytest.mark.asyncio
  async def test_logs_call_uses_stream_and_follow(docker_runner, tmp_path):
      """Verify logs() is called with stream=True and follow=True."""
      stdout_log = tmp_path / "stdout.log"
      stderr_log = tmp_path / "stderr.log"
      workspace = tmp_path / "workspace"
      workspace.mkdir()

      with patch("docker.from_env") as mock_docker:
          mock_client = Mock()
          mock_container = Mock()
          mock_container.wait.return_value = {"StatusCode": 0}
          mock_container.logs.return_value = iter([])
          mock_client.containers.run.return_value = mock_container
          mock_docker.return_value = mock_client

          await docker_runner.run(
              command=["cargo", "rapx"],
              workspace_dir=workspace,
              stdout_log=stdout_log,
              stderr_log=stderr_log,
          )

          # logs() should be called with stream=True and follow=True for real-time output
          calls = mock_container.logs.call_args_list
          assert len(calls) == 2
          for call in calls:
              assert call.kwargs.get("stream") is True
              assert call.kwargs.get("follow") is True


  @pytest.mark.asyncio
  async def test_docker_timeout_stops_container(docker_runner, tmp_path):
      """On timeout, the container is stopped and exit_code is -1."""
      stdout_log = tmp_path / "stdout.log"
      stderr_log = tmp_path / "stderr.log"
      workspace = tmp_path / "workspace"
      workspace.mkdir()

      with patch("docker.from_env") as mock_docker:
          mock_client = Mock()
          mock_container = Mock()
          # Simulate timeout: wait() raises an exception
          mock_container.wait.side_effect = Exception("Read timeout")
          mock_container.logs.return_value = iter([])
          mock_client.containers.run.return_value = mock_container
          mock_docker.return_value = mock_client

          exit_code = await docker_runner.run(
              command=["cargo", "rapx"],
              workspace_dir=workspace,
              stdout_log=stdout_log,
              stderr_log=stderr_log,
          )

          assert exit_code == -1
          mock_container.stop.assert_called_once()
          mock_container.remove.assert_called_once()
  ```

- [ ] **Step 3: Run new tests to verify they FAIL (tests describe behavior not yet implemented)**

  ```bash
  cd backend && uv run pytest tests/unit/test_docker_runner.py::test_logs_file_content_correct_after_streaming tests/unit/test_docker_runner.py::test_logs_call_uses_stream_and_follow tests/unit/test_docker_runner.py::test_docker_timeout_stops_container -v
  ```

  Expected: FAIL. `test_logs_call_uses_stream_and_follow` will fail because the current code calls `container.logs()` without `stream=True`/`follow=True`. `test_logs_file_content_correct_after_streaming` will fail because the current code uses `write_text()` on a non-iterator return value.

---

### Task 2: Implement streaming in DockerRunner

**Files:**
- Modify: `backend/app/utils/docker_runner.py`

- [ ] **Step 1: Read the current `run()` method**

  Lines 83–159 of `backend/app/utils/docker_runner.py`. Key section to replace is lines 126–146:
  ```python
  # Wait for container with timeout
  timeout_seconds = self.max_runtime_hours * 3600
  try:
      result = container.wait(timeout=timeout_seconds)
      exit_code = result.get("StatusCode", -1)
  except Exception:
      # Timeout or error - stop the container
      container.stop(timeout=10)
      exit_code = -1

  # Get logs
  logs = container.logs(stdout=True, stderr=False).decode(...)
  stderr_logs = container.logs(stdout=False, stderr=True).decode(...)

  # Write logs to files
  stdout_log.write_text(logs)
  stderr_log.write_text(stderr_logs)
  ```

- [ ] **Step 2: Replace the `run()` method with streaming implementation**

  Replace the full `run()` method body (keeping the signature and docstring). The new implementation:

  ```python
  async def run(
      self,
      command: List[str],
      workspace_dir: Path,
      stdout_log: Path,
      stderr_log: Path,
  ) -> int:
      """
      Run a command in a Docker container with resource limits.

      Args:
          command: Command and arguments to execute
          workspace_dir: Host path to mount as /workspace in container
          stdout_log: Path to write stdout
          stderr_log: Path to write stderr

      Returns:
          Container exit code
      """
      # Ensure workspace directory exists
      workspace_dir.mkdir(parents=True, exist_ok=True)
      stdout_log.parent.mkdir(parents=True, exist_ok=True)
      stderr_log.parent.mkdir(parents=True, exist_ok=True)

      # Build resource limits
      resource_limits = self._build_resource_limits()

      # Prepare volume mounts
      volumes = {str(workspace_dir.resolve()): {"bind": "/workspace", "mode": "rw"}}

      # Run container
      try:
          container = self.client.containers.run(
              image=self.image,
              command=command,
              working_dir="/workspace",
              volumes=volumes,
              detach=True,
              stdout=True,
              stderr=True,
              **resource_limits,
          )

          timeout_seconds = self.max_runtime_hours * 3600
          loop = asyncio.get_running_loop()

          def _stream_to_file(log_path: Path, log_kwargs: dict) -> None:
              """Stream Docker logs to a file, writing each chunk as it arrives.

              log_kwargs is passed as a plain positional arg because
              run_in_executor() does not support keyword argument forwarding.
              """
              with log_path.open("w", encoding="utf-8", errors="replace") as f:
                  for chunk in container.logs(stream=True, follow=True, **log_kwargs):
                      if isinstance(chunk, bytes):
                          f.write(chunk.decode("utf-8", errors="replace"))
                      else:
                          f.write(chunk)
                      f.flush()

          def _wait_container() -> int:
              try:
                  result = container.wait(timeout=timeout_seconds)
                  return result.get("StatusCode", -1)
              except Exception:
                  container.stop(timeout=10)
                  return -1

          # Run wait and both log streams concurrently in thread executors.
          # Each runs in a separate thread since Docker SDK is synchronous.
          # IMPORTANT: pass log_kwargs as a positional arg — run_in_executor
          # does not support **kwargs forwarding.
          wait_future = loop.run_in_executor(None, _wait_container)
          stdout_future = loop.run_in_executor(
              None, _stream_to_file, stdout_log, {"stdout": True, "stderr": False}
          )
          stderr_future = loop.run_in_executor(
              None, _stream_to_file, stderr_log, {"stdout": False, "stderr": True}
          )

          results = await asyncio.gather(
              wait_future, stdout_future, stderr_future, return_exceptions=True
          )
          exit_code = results[0] if not isinstance(results[0], Exception) else -1

          # Cleanup
          try:
              container.remove(force=True)
          except Exception:
              pass

          return exit_code

      except Exception as e:
          stderr_log.write_text(f"Unexpected error: {e}")
          return -1
  ```

  **Key differences from old code:**
  - `_stream_to_file()` opens the log file in write mode immediately, then iterates the generator yielding real-time chunks, flushing after each chunk
  - `_wait_container()` handles timeout by stopping the container and returning -1
  - All three (wait, stdout stream, stderr stream) run concurrently via `run_in_executor`; the log streams naturally terminate when the container exits because `follow=True` stops when the container stops

- [ ] **Step 3: Run the new tests to verify they PASS**

  ```bash
  cd backend && uv run pytest tests/unit/test_docker_runner.py -v
  ```

  Expected output:
  ```
  PASSED tests/unit/test_docker_runner.py::test_docker_runner_initialization
  PASSED tests/unit/test_docker_runner.py::test_run_builds_correct_command
  PASSED tests/unit/test_docker_runner.py::test_ensure_image_with_if_not_present_policy
  PASSED tests/unit/test_docker_runner.py::test_ensure_image_pulls_when_missing
  PASSED tests/unit/test_docker_runner.py::test_logs_file_content_correct_after_streaming
  PASSED tests/unit/test_docker_runner.py::test_logs_call_uses_stream_and_follow
  PASSED tests/unit/test_docker_runner.py::test_docker_timeout_stops_container
  ```

  **Note:** The existing `test_run_builds_correct_command` test uses `mock_container.logs.return_value = b"test output"`. This **must** be updated to `iter([b"test output"])` before the new implementation will pass — iterating raw `bytes` yields integers (not byte strings), which crashes `f.write()`. Update that mock unconditionally alongside the new tests.

- [ ] **Step 4: Run the full unit test suite to ensure no regressions**

  ```bash
  cd backend && uv run pytest tests/unit/ -v
  ```

  All tests should pass. If `test_task_executor.py` tests fail due to Docker mode changes, check that the executor tests mock `docker_runner.run` at the `AsyncMock` level (they do — `mock_runner.run = AsyncMock(return_value=0)`), so they should be unaffected.

- [ ] **Step 5: Format code with Black**

  ```bash
  cd backend && uv run black app/utils/docker_runner.py tests/unit/test_docker_runner.py
  ```

  No output means formatting passed. If changes are made, the file is reformatted — that's expected and correct.

- [ ] **Step 6: Verify formatting didn't break tests**

  ```bash
  cd backend && uv run pytest tests/unit/test_docker_runner.py -v
  ```

  Expected: All tests pass.

- [ ] **Step 7: Commit**

  ```bash
  git add backend/app/utils/docker_runner.py backend/tests/unit/test_docker_runner.py
  git commit -m "fix: stream Docker logs in real-time instead of post-hoc collection

  Previously, DockerRunner.run() blocked on container.wait() and then
  called container.logs() once after the container exited. This meant
  log files were empty during the entire container run, so the REST log
  endpoints returned nothing for running Docker tasks.

  Now, stdout and stderr are streamed concurrently via Docker SDK's
  logs(stream=True, follow=True), writing each chunk to disk as it
  arrives. container.wait() runs in parallel in its own thread executor.
  All three are gathered with asyncio.gather(), preserving the existing
  exit-code contract."
  ```

---

## Chunk 2: Edge Case Handling and Integration Verification

### Task 3: Verify integration with TaskExecutor and REST endpoints

**Files:**
- Read: `backend/app/services/task_executor.py` (no changes needed, verification only)
- Read: `backend/tests/unit/test_task_executor.py` (no changes needed, verification only)

- [ ] **Step 1: Run the full test suite (all unit + integration tests)**

  ```bash
  cd backend && uv run pytest -v
  ```

  All previously passing tests should still pass. Docker-specific executor tests in `test_task_executor.py` mock `docker_runner.run` entirely (`mock_runner.run = AsyncMock(return_value=0)`), so they are unaffected by internal DockerRunner changes.

- [ ] **Step 2: Manual smoke check (optional, requires Docker)**

  If Docker is available in the dev environment:
  ```bash
  # Start the backend
  cd backend && uv run python -m app.main

  # In another terminal, create a task with docker execution_mode in config.toml
  # Then watch the log file grow in real-time during execution:
  tail -f workspace/logs/<crate-name>-<version>-stdout.log
  ```

  You should see log lines appear as cargo rapx runs, not only after it finishes.

---

## Summary of Changes

| File | Changes |
|------|---------|
| `backend/app/utils/docker_runner.py` | Replace `wait()+write_text()` with three concurrent `run_in_executor` tasks: `_wait_container()`, `_stream_to_file(stdout)`, `_stream_to_file(stderr)` |
| `backend/tests/unit/test_docker_runner.py` | Update existing mock from `b"bytes"` to `iter([b"bytes"])`; add 3 new tests for streaming behavior, `stream=True`/`follow=True` flags, and timeout handling |

No changes to `main.py`, `task_executor.py`, frontend, or database. The fix is fully contained in `docker_runner.py` and its test file.
