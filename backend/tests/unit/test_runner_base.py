from app.models import TaskStatus
from app.utils.runner_base import ExecutionResult


def test_execution_result_creation():
    """ExecutionResult can be created with all fields."""
    result = ExecutionResult(
        state=TaskStatus.COMPLETED,
        exit_code=0,
        message="Completed successfully",
    )

    assert result.state == TaskStatus.COMPLETED
    assert result.exit_code == 0
    assert result.message == "Completed successfully"


def test_execution_result_default_message():
    """ExecutionResult message defaults to empty string."""
    result = ExecutionResult(state=TaskStatus.FAILED, exit_code=1)

    assert result.message == ""


def test_execution_result_for_timeout():
    """ExecutionResult supports timeout scenario details."""
    result = ExecutionResult(
        state=TaskStatus.TIMEOUT,
        exit_code=-1,
        message="Execution timed out after 7200 seconds",
    )

    assert result.state == TaskStatus.TIMEOUT


def test_execution_result_for_oom():
    """ExecutionResult supports OOM scenario details."""
    result = ExecutionResult(
        state=TaskStatus.OOM,
        exit_code=137,
        message="Process killed by OOM killer (out of memory)",
    )

    assert result.state == TaskStatus.OOM
