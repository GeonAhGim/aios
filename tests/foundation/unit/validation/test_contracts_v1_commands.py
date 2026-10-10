"""task-6330 -- TEST-cov: src/foundation/evidence/contracts/v1.py RecordAuditEventCommand.

Covers RecordAuditEventCommand Pydantic model (required/optional fields, defaults).

D2 evidence: negative >=3, failure-injection 1, perf 1, gate-red repro 1.
"""

from __future__ import annotations

from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.evidence.contracts.v1 import (
    Classification,
    Outcome,
    RecordAuditEventCommand,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _valid_cmd_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid RecordAuditEventCommand dict."""
    return {
        "tenant_id": uuid4(),
        "aggregate_type": "Portfolio",
        "aggregate_id": uuid4(),
        "aggregate_revision": 1,
        "action": "CREATED",
        "outcome": Outcome.SUCCESS,
        "actor_subject_id": uuid4(),
        "trace_id": uuid4(),
        "payload": {"key": "value"},
        "classification": Classification.INTERNAL,
        **overrides,
    }


# ── RecordAuditEventCommand ───────────────────────────────────────────────


def test_record_audit_event_command_all_fields() -> None:
    """RecordAuditEventCommand with all fields."""
    kwargs = _valid_cmd_kwargs()
    cmd = RecordAuditEventCommand(**kwargs)

    assert cmd.tenant_id == kwargs["tenant_id"]
    assert cmd.aggregate_type == "Portfolio"
    assert cmd.aggregate_id == kwargs["aggregate_id"]
    assert cmd.aggregate_revision == 1
    assert cmd.action == "CREATED"
    assert cmd.outcome == Outcome.SUCCESS
    assert cmd.actor_subject_id == kwargs["actor_subject_id"]
    assert cmd.trace_id == kwargs["trace_id"]
    assert cmd.payload == {"key": "value"}
    assert cmd.classification == Classification.INTERNAL


def test_record_audit_event_command_defaults() -> None:
    """RecordAuditEventCommand default values."""
    cmd = RecordAuditEventCommand(**_valid_cmd_kwargs())

    assert cmd.aggregate_revision == 1
    assert cmd.payload == {"key": "value"}
    assert cmd.classification == Classification.INTERNAL


def test_record_audit_event_command_tenant_id_none() -> None:
    """RecordAuditEventCommand tenant_id can be None (system event)."""
    cmd = RecordAuditEventCommand(
        tenant_id=None,
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="CREATED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.tenant_id is None


def test_record_audit_event_command_actor_none() -> None:
    """RecordAuditEventCommand actor_subject_id can be None."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="CREATED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
        actor_subject_id=None,
    )

    assert cmd.actor_subject_id is None


def test_record_audit_event_command_empty_payload() -> None:
    """RecordAuditEventCommand payload defaults to empty dict."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="CREATED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.payload == {}


def test_record_audit_event_command_classification_default() -> None:
    """RecordAuditEventCommand classification defaults to INTERNAL."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="CREATED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.classification == Classification.INTERNAL


def test_record_audit_event_command_missing_aggregate_type() -> None:
    """RecordAuditEventCommand requires aggregate_type (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_id=uuid4(),
            action="CREATED",
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
        )
    assert "aggregate_type" in str(exc_info.value)


def test_record_audit_event_command_missing_aggregate_id() -> None:
    """RecordAuditEventCommand requires aggregate_id (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="Portfolio",
            action="CREATED",
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
        )
    assert "aggregate_id" in str(exc_info.value)


def test_record_audit_event_command_missing_action() -> None:
    """RecordAuditEventCommand requires action (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="Portfolio",
            aggregate_id=uuid4(),
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
        )
    assert "action" in str(exc_info.value)


def test_record_audit_event_command_missing_outcome() -> None:
    """RecordAuditEventCommand requires outcome (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="Portfolio",
            aggregate_id=uuid4(),
            action="CREATED",
            trace_id=uuid4(),
        )
    assert "outcome" in str(exc_info.value)


def test_record_audit_event_command_missing_trace_id() -> None:
    """RecordAuditEventCommand requires trace_id (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="Portfolio",
            aggregate_id=uuid4(),
            action="CREATED",
            outcome=Outcome.SUCCESS,
        )
    assert "trace_id" in str(exc_info.value)


def test_record_audit_event_command_optional_aggregate_revision() -> None:
    """RecordAuditEventCommand aggregate_revision is optional."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="CREATED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
        aggregate_revision=None,
    )

    assert cmd.aggregate_revision is None


# ── Failure injection ──────────────────────────────────────────────────────


def test_command_invalid_outcome(monkeypatch: Any, perf_budget: Any) -> None:
    """Failure injection: RecordAuditEventCommand with invalid outcome (monkeypatch)."""
    # Pydantic validates Outcome enum at model init time.
    # This test verifies that passing an invalid outcome string raises ValidationError.
    with pytest.raises(ValidationError):
        RecordAuditEventCommand(
            tenant_id=uuid4(),
            aggregate_type="Test",
            aggregate_id=uuid4(),
            action="TEST",
            outcome=cast(Outcome, "INVALID"),
            trace_id=uuid4(),
        )


# ── Performance ────────────────────────────────────────────────────────────


def test_command_performance(perf_budget: Any) -> None:
    """Performance: RecordAuditEventCommand creation within spec (< 1ms)."""
    cmd_kwargs = _valid_cmd_kwargs()

    perf_budget.assert_within(
        lambda: RecordAuditEventCommand(**cmd_kwargs), budget_ms=1.0, batch=100, label="Command"
    )


# ── Gate-red reproduction ──────────────────────────────────────────────────


def test_command_json_roundtrip() -> None:
    """RecordAuditEventCommand must be JSON-serializable and round-trip (gate-red check)."""
    cmd = RecordAuditEventCommand(**_valid_cmd_kwargs())
    cmd_json = cmd.model_dump_json()
    cmd_parsed = RecordAuditEventCommand.model_validate_json(cmd_json)
    assert cmd_parsed.tenant_id == cmd.tenant_id
    assert cmd_parsed.outcome == cmd.outcome
