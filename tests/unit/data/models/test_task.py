from src.data.models.task import AIOSTask, TaskStatus


def test_aios_task_defaults():
    task = AIOSTask(
        objective="scan market", assigned_agent="scanner-1", required_permission_level=2
    )
    assert task.status == TaskStatus.PENDING.value
    assert task.retry_count == 0
    assert task.parent_task_id is None
    assert task.task_id is not None


def test_aios_task_permission_level_bounds():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AIOSTask(objective="x", assigned_agent="a", required_permission_level=7)
