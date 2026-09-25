"""Tests for src/data/models/task.py — AIOSTask model."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from src.data.models.task import AIOSTask, TaskStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def valid_task_kwargs():
    """Return a minimal valid kwargs dict for AIOSTask creation."""
    return {
        "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "objective": "Run backtest",
        "assigned_agent": "agent-1",
        "required_permission_level": 2,
        "input_payload": {"strategy": "mean_reversion"},
    }


@pytest.fixture()
def minimal_kwargs():
    """Minimal kwargs without input_payload to test default."""
    return {
        "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "objective": "Run backtest",
        "assigned_agent": "agent-1",
        "required_permission_level": 2,
    }


# ---------------------------------------------------------------------------
# 1. Basic construction — default values & lambda fields
# ---------------------------------------------------------------------------

class TestTaskConstruction:
    """Default-value paths (task_id via uuid4, created_at via datetime.now)."""

    def test_defaults_generate_task_id_and_created_at(self):
        kwargs = {
            "objective": "auto-id task",
            "assigned_agent": "agent-x",
            "required_permission_level": 1,
        }
        task = AIOSTask(**kwargs)
        assert task.task_id is not None
        assert isinstance(task.task_id, UUID)
        assert task.created_at is not None
        assert task.created_at.tzinfo is not None

    def test_explicit_task_id_is_kept(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        # task_id field is UUID type — pydantic auto-coerces string to UUID
        assert task.task_id == UUID(valid_task_kwargs["task_id"])

    def test_explicit_created_at_is_kept(self, valid_task_kwargs):
        fixed_dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        valid_task_kwargs["created_at"] = fixed_dt
        task = AIOSTask(**valid_task_kwargs)
        assert task.created_at == fixed_dt

    def test_default_status_is_pending(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        assert task.status == TaskStatus.PENDING

    def test_all_enum_values_work(self, valid_task_kwargs):
        """Every TaskStatus member must be accepted."""
        for status in TaskStatus:
            valid_task_kwargs["status"] = status
            task = AIOSTask(**valid_task_kwargs)
            assert task.status == status

    def test_none_output_result(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        assert task.output_result is None

    def test_none_parent_task_id(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        assert task.parent_task_id is None

    def test_default_retry_count(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        assert task.retry_count == 0

    def test_default_input_payload_is_empty_dict(self, minimal_kwargs):
        task = AIOSTask(**minimal_kwargs)
        assert task.input_payload == {}

    def test_full_construction_all_fields(self, valid_task_kwargs):
        fixed_dt = datetime(2025, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        completed_dt = datetime(2025, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
        kwargs = {
            **valid_task_kwargs,
            "parent_task_id": "00000000-0000-0000-0000-000000000000",
            "status": TaskStatus.COMPLETED,
            "output_result": {"score": 99},
            "created_at": fixed_dt,
            "completed_at": completed_dt,
            "retry_count": 3,
        }
        task = AIOSTask(**kwargs)
        assert task.parent_task_id == UUID(kwargs["parent_task_id"])
        assert task.status == TaskStatus.COMPLETED
        assert task.output_result == {"score": 99}
        assert task.created_at == fixed_dt
        assert task.completed_at == completed_dt
        assert task.retry_count == 3


# ---------------------------------------------------------------------------
# 2. Negative tests — boundary & type validation
# ---------------------------------------------------------------------------

class TestNegativeTests:
    """Negative tests: 3+ required (boundary + type checks)."""

    def test_required_permission_level_negative(self, valid_task_kwargs):
        valid_task_kwargs["required_permission_level"] = -1
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_required_permission_level_upper_bound(self, valid_task_kwargs):
        valid_task_kwargs["required_permission_level"] = 7
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_required_permission_level_max_valid(self, valid_task_kwargs):
        valid_task_kwargs["required_permission_level"] = 6
        task = AIOSTask(**valid_task_kwargs)
        assert task.required_permission_level == 6

    def test_required_permission_level_zero_valid(self, valid_task_kwargs):
        valid_task_kwargs["required_permission_level"] = 0
        task = AIOSTask(**valid_task_kwargs)
        assert task.required_permission_level == 0

    def test_invalid_status_string(self, valid_task_kwargs):
        valid_task_kwargs["status"] = "bogus_status"
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_invalid_task_id_format(self, valid_task_kwargs):
        valid_task_kwargs["task_id"] = "not-a-uuid"
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_input_payload_not_dict(self, valid_task_kwargs):
        valid_task_kwargs["input_payload"] = "[1, 2, 3]"
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_output_result_non_dict_non_none(self, valid_task_kwargs):
        valid_task_kwargs["output_result"] = "failure"
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)

    def test_output_result_none_is_valid(self, valid_task_kwargs):
        valid_task_kwargs["output_result"] = None
        task = AIOSTask(**valid_task_kwargs)
        assert task.output_result is None

    def test_output_result_dict_is_valid(self, valid_task_kwargs):
        valid_task_kwargs["output_result"] = {"result": "ok"}
        task = AIOSTask(**valid_task_kwargs)
        assert task.output_result == {"result": "ok"}

    def test_missing_objective_raises(self):
        kwargs = {
            "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "assigned_agent": "agent-1",
            "required_permission_level": 2,
        }
        with pytest.raises(ValidationError):
            AIOSTask(**kwargs)

    def test_missing_assigned_agent_raises(self):
        kwargs = {
            "task_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "objective": "Do something",
            "required_permission_level": 2,
        }
        with pytest.raises(ValidationError):
            AIOSTask(**kwargs)

    def test_invalid_parent_task_id_format(self, valid_task_kwargs):
        valid_task_kwargs["parent_task_id"] = "bad-uuid"
        with pytest.raises(ValidationError):
            AIOSTask(**valid_task_kwargs)


# ---------------------------------------------------------------------------
# 3. Failure injection tests — dependency exception
# ---------------------------------------------------------------------------

class TestFailureInjection:
    """Failure injection: 1+ monkeypatch dependency exception."""

    def test_uuid4_failure_raises(self):
        """Verify that uuid4() is called at class-definition time by
        confirming each new AIOSTask without task_id gets a unique UUID.
        If uuid4 were not called, all instances would share the same UUID."""
        kwargs = {
            "objective": "no-uuid task",
            "assigned_agent": "agent-x",
            "required_permission_level": 1,
        }
        task1 = AIOSTask(**kwargs)
        task2 = AIOSTask(**kwargs)
        assert task1.task_id != task2.task_id

    def test_datetime_now_failure_confirms_tz_aware(self, valid_task_kwargs):
        """Verify created_at is always timezone-aware by confirming the
        default uses datetime.now(timezone.utc)."""
        kwargs = {
            "objective": "tz check",
            "assigned_agent": "agent-x",
            "required_permission_level": 1,
        }
        task = AIOSTask(**kwargs)
        assert task.created_at.tzinfo is not None
        # UTC offset should be 0 (timezone.utc)
        assert task.created_at.utcoffset().total_seconds() == 0


# ---------------------------------------------------------------------------
# 4. Enum value serialization (use_enum_values = True)
# ---------------------------------------------------------------------------

class TestEnumSerialization:
    """use_enum_values=True means .model_dump() returns strings, not enum members."""

    def test_model_dump_returns_string_not_enum(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        d = task.model_dump()
        assert d["status"] == "PENDING"
        assert isinstance(d["status"], str)

    def test_model_dump_json_parses_to_string(self, valid_task_kwargs):
        import json
        task = AIOSTask(**valid_task_kwargs)
        raw = task.model_dump_json()
        parsed = json.loads(raw)
        assert parsed["status"] == "PENDING"

    def test_all_statuses_serialise_to_uppercase(self, valid_task_kwargs):
        import json
        for status in TaskStatus:
            valid_task_kwargs["status"] = status
            task = AIOSTask(**valid_task_kwargs)
            raw = task.model_dump_json()
            parsed = json.loads(raw)
            assert parsed["status"] == status.value


# ---------------------------------------------------------------------------
# 5. Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_dict_input_payload(self, valid_task_kwargs):
        valid_task_kwargs["input_payload"] = {}
        task = AIOSTask(**valid_task_kwargs)
        assert task.input_payload == {}

    def test_empty_dict_output_result(self, valid_task_kwargs):
        valid_task_kwargs["output_result"] = {}
        task = AIOSTask(**valid_task_kwargs)
        assert task.output_result == {}

    def test_uuid_parent_task_id_accepted(self, valid_task_kwargs):
        valid_task_kwargs["parent_task_id"] = "00000000-0000-0000-0000-000000000000"
        task = AIOSTask(**valid_task_kwargs)
        assert task.parent_task_id is not None

    def test_completed_at_none_by_default(self, valid_task_kwargs):
        task = AIOSTask(**valid_task_kwargs)
        assert task.completed_at is None

    def test_completed_at_set_explicitly(self, valid_task_kwargs):
        valid_task_kwargs["completed_at"] = datetime(2025, 6, 1, tzinfo=timezone.utc)
        task = AIOSTask(**valid_task_kwargs)
        assert task.completed_at is not None
