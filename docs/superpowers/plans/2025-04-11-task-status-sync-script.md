# Task Status Sync Script Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建一个命令行脚本，检查数据库中标记为 running 但实际进程已不存在的任务，将其状态同步为 failed。

**Architecture:** 复用现有的 `Database` 和 `Config` 类，查询 running 状态任务，通过 `os.kill(pid, 0)` 检查进程是否存在，对不存在进程的任务更新状态为 failed。

**Tech Stack:** Python, SQLite, os module

---

## File Structure

- **新建:** `backend/scripts/sync_task_status.py` - 主脚本文件
- **复用:** `backend/app/database.py` - 数据库操作
- **复用:** `backend/app/config.py` - 配置加载
- **复用:** `backend/app/models.py` - TaskStatus 枚举

---

### Task 1: Create Sync Script

**Files:**
- Create: `backend/scripts/sync_task_status.py`

- [ ] **Step 1: Create script file with basic structure**

```python
#!/usr/bin/env python3
"""
Task Status Sync Script

检查数据库中标记为 running 但实际进程已不存在的任务，
将其状态同步为 failed。

Usage:
    cd backend && python scripts/sync_task_status.py
    cd backend && python scripts/sync_task_status.py --dry-run  # 只检查不修复
"""

import os
import sys
import argparse
from pathlib import Path
from datetime import datetime

# Add parent directory to path to import app modules
script_dir = Path(__file__).parent.resolve()
backend_dir = script_dir.parent
sys.path.insert(0, str(backend_dir))

from app.database import Database
from app.config import Config
from app.models import TaskStatus


def is_process_running(pid: int) -> bool:
    """Check if a process with given PID exists."""
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def sync_task_status(dry_run: bool = False) -> None:
    """
    Check all running tasks and update status for those whose process no longer exists.

    Args:
        dry_run: If True, only report issues without fixing them.
    """
    # Load configuration
    config_path = backend_dir / ".." / "config.toml"
    if not config_path.exists():
        config_path = backend_dir / "config.toml"

    config = Config.from_file(config_path)
    db = Database(str(config.database_path))
    db.init_db()

    # Get all running tasks
    running_tasks = db.get_tasks_by_status(TaskStatus.RUNNING)

    if not running_tasks:
        print("No running tasks found.")
        return

    print(f"Found {len(running_tasks)} task(s) in RUNNING state.")
    print("-" * 60)

    inconsistent_count = 0

    for task in running_tasks:
        task_info = f"Task {task.id}: {task.crate_name} {task.version}"

        if not task.pid:
            print(f"{task_info}")
            print(f"  PID: not set")
            print(f"  Status: RUNNING but no PID recorded")
            inconsistent_count += 1

            if not dry_run:
                db.update_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    finished_at=datetime.now(),
                    error_message="Task has no PID recorded but status is RUNNING",
                )
                print(f"  Action: Marked as FAILED")
            else:
                print(f"  Action: would mark as FAILED (dry-run)")
            print()
            continue

        # Check if process is running
        if is_process_running(task.pid):
            print(f"{task_info}")
            print(f"  PID: {task.pid}")
            print(f"  Status: OK - process is running")
            print()
        else:
            print(f"{task_info}")
            print(f"  PID: {task.pid}")
            print(f"  Status: INCONSISTENT - process not found")
            inconsistent_count += 1

            if not dry_run:
                db.update_task_status(
                    task.id,
                    TaskStatus.FAILED,
                    finished_at=datetime.now(),
                    error_message="Process terminated unexpectedly (detected by sync script)",
                )
                print(f"  Action: Marked as FAILED")
            else:
                print(f"  Action: would mark as FAILED (dry-run)")
            print()

    print("-" * 60)
    if dry_run:
        print(f"Dry-run complete. Found {inconsistent_count} inconsistent task(s).")
    else:
        print(f"Sync complete. Fixed {inconsistent_count} inconsistent task(s).")


def main():
    parser = argparse.ArgumentParser(
        description="Sync task status - mark running tasks as failed if their process no longer exists."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report issues without fixing them",
    )

    args = parser.parse_args()
    sync_task_status(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Make script executable**

Run: `chmod +x backend/scripts/sync_task_status.py`

Expected: File permissions updated successfully.

- [ ] **Step 3: Test the script (dry-run mode)**

Run:
```bash
cd backend
python scripts/sync_task_status.py --dry-run
```

Expected: Script runs without errors, reports status of running tasks.

- [ ] **Step 4: Test the script (actual fix mode)**

Run:
```bash
cd backend
python scripts/sync_task_status.py
```

Expected: Script runs without errors, fixes inconsistent tasks (if any).

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/sync_task_status.py
git commit -m "feat(scripts): add task status sync script

Add script to detect and fix tasks stuck in RUNNING state
when their process no longer exists. Supports dry-run mode
for safe inspection before fixing."
```

---

## Self-Review

**Spec coverage:**
- ✅ 扫描 running 状态任务 - covered in Step 1
- ✅ 检查进程是否存在 - covered by `is_process_running()` function
- ✅ 直接标记为 failed - covered in sync logic
- ✅ 命令行工具 - covered by argparse
- ✅ 可选 dry-run 模式 - covered by `--dry-run` flag

**Placeholder scan:**
- ✅ No TBD/TODO/fill in later patterns
- ✅ All code is complete and ready to use

**Type consistency:**
- ✅ Uses existing `TaskStatus.FAILED` from models
- ✅ Uses existing `Database.get_tasks_by_status()` method
- ✅ Uses existing `Database.update_task_status()` method
