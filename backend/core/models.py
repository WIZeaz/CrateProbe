from enum import Enum
from dataclasses import dataclass


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"
    OOM = "oom"


@dataclass
class ExecutionResult:
    state: TaskStatus
    exit_code: int
    message: str = ""
