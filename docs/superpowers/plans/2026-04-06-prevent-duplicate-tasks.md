# 防止重复创建任务 - 实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在创建新任务时，检查是否已存在相同 crate 和 version 的任务，如果存在则返回现有任务而不是创建新任务。

**Architecture:** 在 API 层（main.py）进行检查，先查询数据库是否存在相同 (crate_name, version) 的任务，如果存在则直接返回，否则创建新任务。需要在 database.py 中添加查询方法。

**Tech Stack:** Python, FastAPI, SQLite

---

## Chunk 1: 数据库层添加查询方法

**Files:**
- Modify: `backend/app/database.py`
- Test: `backend/tests/unit/test_database.py`

### Task 1.1: 添加 `get_task_by_crate_and_version` 方法

- [ ] **Step 1: 编写测试**

在 `backend/tests/unit/test_database.py` 中添加测试：

```python
def test_get_task_by_crate_and_version_returns_task(db, tmp_path):
    """Test retrieving task by crate name and version"""
    db.init_db()

    # Create a task
    task_id = db.create_task(
        crate_name="test-crate",
        version="1.0.0",
        workspace_path=str(tmp_path / "workspace"),
        stdout_log=str(tmp_path / "stdout.log"),
        stderr_log=str(tmp_path / "stderr.log"),
    )

    # Retrieve by crate and version
    task = db.get_task_by_crate_and_version("test-crate", "1.0.0")

    assert task is not None
    assert task.id == task_id
    assert task.crate_name == "test-crate"
    assert task.version == "1.0.0"


def test_get_task_by_crate_and_version_returns_none_when_not_found(db, tmp_path):
    """Test retrieving non-existent task returns None"""
    db.init_db()

    # Try to retrieve non-existent task
    task = db.get_task_by_crate_and_version("non-existent", "1.0.0")

    assert task is None


def test_get_task_by_crate_and_version_requires_exact_match(db, tmp_path):
    """Test that crate name and version must match exactly"""
    db.init_db()

    # Create a task
    db.create_task(
        crate_name="test-crate",
        version="1.0.0",
        workspace_path=str(tmp_path / "workspace"),
        stdout_log=str(tmp_path / "stdout.log"),
        stderr_log=str(tmp_path / "stderr.log"),
    )

    # Different crate name
    assert db.get_task_by_crate_and_version("other-crate", "1.0.0") is None

    # Different version
    assert db.get_task_by_crate_and_version("test-crate", "2.0.0") is None
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend
uv run pytest tests/unit/test_database.py::test_get_task_by_crate_and_version_returns_task -v
```

Expected: FAIL - `AttributeError: 'Database' object has no attribute 'get_task_by_crate_and_version'`

- [ ] **Step 3: 实现查询方法**

在 `backend/app/database.py` 的 `Database` 类中添加方法（放在 `get_task` 方法之后）：

```python
def get_task_by_crate_and_version(
    self, crate_name: str, version: str
) -> Optional[TaskRecord]:
    """Get a task by crate name and version

    Args:
        crate_name: Name of the crate
        version: Version of the crate

    Returns:
        TaskRecord if found, None otherwise
    """
    cursor = self.conn.cursor()
    cursor.execute(
        "SELECT * FROM tasks WHERE crate_name = ? AND version = ?",
        (crate_name, version),
    )
    row = cursor.fetchone()

    if row is None:
        return None

    return self._row_to_task_record(row)
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend
uv run pytest tests/unit/test_database.py -k "get_task_by_crate_and_version" -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 格式化并提交**

```bash
cd backend
uv run black app/ tests/
git add app/database.py tests/unit/test_database.py
git commit -m "feat(database): add get_task_by_crate_and_version method"
```

---

## Chunk 2: API 层修改任务创建逻辑

**Files:**
- Modify: `backend/app/main.py`
- Test: `backend/tests/unit/test_main.py` (需要创建或修改)

### Task 2.1: 修改 `create_task` 端点实现防重复逻辑

- [ ] **Step 1: 编写测试**

如果 `backend/tests/unit/test_main.py` 不存在，先创建它：

```python
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config


@pytest.fixture
def test_config(tmp_path):
    """Create a test configuration"""
    config = Config(
        workspace_path=tmp_path / "workspace",
        max_memory_gb=1,
        max_runtime_hours=1,
        max_jobs=1,
        server_host="127.0.0.1",
        server_port=8000,
        log_level="INFO",
        use_docker=False,
    )
    return config


@pytest.fixture
def test_db_path(tmp_path):
    """Create a test database path"""
    return str(tmp_path / "test.db")


@pytest.fixture
def client(test_config, test_db_path):
    """Create a test client"""
    app = create_app(test_config, test_db_path)
    return TestClient(app)
```

然后添加防重复测试：

```python
def test_create_task_returns_existing_task_if_duplicate(client, monkeypatch):
    """Test that creating a task with same crate and version returns existing task"""

    # Mock crates API to avoid external calls
    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(
        "app.main.CratesAPI", lambda: MockCratesAPI()
    )

    # Create first task
    response1 = client.post("/api/tasks", json={
        "crate_name": "test-crate",
        "version": "1.0.0"
    })
    assert response1.status_code == 200
    task1 = response1.json()
    task1_id = task1["task_id"]

    # Create second task with same crate and version
    response2 = client.post("/api/tasks", json={
        "crate_name": "test-crate",
        "version": "1.0.0"
    })
    assert response2.status_code == 200
    task2 = response2.json()

    # Should return the same task
    assert task2["task_id"] == task1_id
    assert task2["crate_name"] == "test-crate"
    assert task2["version"] == "1.0.0"


def test_create_task_allows_different_versions(client, monkeypatch):
    """Test that different versions of same crate can be created"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(
        "app.main.CratesAPI", lambda: MockCratesAPI()
    )

    # Create task for version 1.0.0
    response1 = client.post("/api/tasks", json={
        "crate_name": "test-crate",
        "version": "1.0.0"
    })
    assert response1.status_code == 200
    task1_id = response1.json()["task_id"]

    # Create task for version 2.0.0
    response2 = client.post("/api/tasks", json={
        "crate_name": "test-crate",
        "version": "2.0.0"
    })
    assert response2.status_code == 200
    task2_id = response2.json()["task_id"]

    # Should be different tasks
    assert task1_id != task2_id


def test_create_task_allows_different_crates_same_version(client, monkeypatch):
    """Test that different crates with same version can be created"""

    class MockCratesAPI:
        async def get_latest_version(self, crate_name):
            return "1.0.0"

        async def verify_version_exists(self, crate_name, version):
            return True

        async def close(self):
            pass

    monkeypatch.setattr(
        "app.main.CratesAPI", lambda: MockCratesAPI()
    )

    # Create task for crate-a
    response1 = client.post("/api/tasks", json={
        "crate_name": "crate-a",
        "version": "1.0.0"
    })
    assert response1.status_code == 200
    task1_id = response1.json()["task_id"]

    # Create task for crate-b
    response2 = client.post("/api/tasks", json={
        "crate_name": "crate-b",
        "version": "1.0.0"
    })
    assert response2.status_code == 200
    task2_id = response2.json()["task_id"]

    # Should be different tasks
    assert task1_id != task2_id
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd backend
uv run pytest tests/unit/test_main.py -k "duplicate or different_versions or different_crates" -v
```

Expected: FAIL - 第二个测试应该失败，因为现在会创建重复任务

- [ ] **Step 3: 修改 `create_task` 端点**

在 `backend/app/main.py` 的 `create_task` 函数中，在调用 `db.create_task` 之前添加检查逻辑：

找到这段代码（约第 120-135 行）：
```python
        try:
            # Get version if not specified
            version = request.version
            if not version:
                version = await crates_api.get_latest_version(request.crate_name)
            else:
                # Verify version exists
                exists = await crates_api.verify_version_exists(
                    request.crate_name, version
                )
                if not exists:
                    raise HTTPException(
                        status_code=400, detail=f"Version {version} not found"
                    )
```

在后面添加检查：
```python
            # Check if task already exists for this crate and version
            existing_task = db.get_task_by_crate_and_version(
                request.crate_name, version
            )
            if existing_task:
                return TaskResponse(
                    task_id=existing_task.id,
                    crate_name=existing_task.crate_name,
                    version=existing_task.version,
                    status=existing_task.status.value,
                )
```

完整修改后的代码结构应该是：
```python
    @app.post("/api/tasks", response_model=TaskResponse)
    async def create_task(request: CreateTaskRequest):
        """Create a new task"""
        crates_api = CratesAPI()

        try:
            # Get version if not specified
            version = request.version
            if not version:
                version = await crates_api.get_latest_version(request.crate_name)
            else:
                # Verify version exists
                exists = await crates_api.verify_version_exists(
                    request.crate_name, version
                )
                if not exists:
                    raise HTTPException(
                        status_code=400, detail=f"Version {version} not found"
                    )

            # Check if task already exists for this crate and version
            existing_task = db.get_task_by_crate_and_version(
                request.crate_name, version
            )
            if existing_task:
                return TaskResponse(
                    task_id=existing_task.id,
                    crate_name=existing_task.crate_name,
                    version=existing_task.version,
                    status=existing_task.status.value,
                )

            # Create workspace paths
            workspace_path = (
                config.workspace_path / "repos" / f"{request.crate_name}-{version}"
            )
            stdout_log = (
                config.workspace_path
                / "logs"
                / f"{request.crate_name}-{version}-stdout.log"
            )
            stderr_log = (
                config.workspace_path
                / "logs"
                / f"{request.crate_name}-{version}-stderr.log"
            )

            # Create task in database
            task_id = db.create_task(
                crate_name=request.crate_name,
                version=version,
                workspace_path=str(workspace_path),
                stdout_log=str(stdout_log),
                stderr_log=str(stderr_log),
            )

            return TaskResponse(
                task_id=task_id,
                crate_name=request.crate_name,
                version=version,
                status=TaskStatus.PENDING.value,
            )

        except CrateNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        finally:
            await crates_api.close()
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd backend
uv run pytest tests/unit/test_main.py -k "duplicate or different_versions or different_crates" -v
```

Expected: PASS (3 tests)

- [ ] **Step 5: 运行所有测试确保没有破坏现有功能**

```bash
cd backend
uv run pytest tests/unit/ -v
```

Expected: 所有测试通过

- [ ] **Step 6: 格式化并提交**

```bash
cd backend
uv run black app/ tests/
git add app/main.py tests/unit/test_main.py
git commit -m "feat(api): prevent duplicate task creation for same crate and version

When creating a task, check if a task already exists for the same
crate name and version. If so, return the existing task instead of
creating a new one."
```

---

## Summary

这个实现计划包含两个主要部分：

1. **数据库层**: 添加 `get_task_by_crate_and_version` 方法用于查询已存在的任务
2. **API 层**: 修改 `create_task` 端点，在创建任务前检查是否已存在相同 crate 和 version 的任务

关键行为：
- 如果任务已存在，返回现有任务（200 OK）
- 不同 version 的相同 crate 可以创建多个任务
- 不同 crate 使用相同 version 可以创建多个任务
- 只有 (crate_name, version) 完全匹配时才视为重复
