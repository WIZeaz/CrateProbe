from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.models import TaskStatus


@dataclass
class ExecutionResult:
    """Structured command execution result."""

    state: TaskStatus
    exit_code: int
    message: str = ""


class Runner(ABC):
    """Abstract runner contract used by task executors."""

    @abstractmethod
    async def run(
        self,
        command: list[str],
        cwd: str,
        timeout_seconds: int,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run a command and return a structured execution result."""
