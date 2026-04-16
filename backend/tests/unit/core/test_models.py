from core.models import TaskStatus, ExecutionResult


def test_task_status_values():
    assert TaskStatus.PENDING.value == "pending"
    assert TaskStatus.RUNNING.value == "running"


def test_execution_result_defaults():
    result = ExecutionResult(state=TaskStatus.COMPLETED, exit_code=0)
    assert result.message == ""
