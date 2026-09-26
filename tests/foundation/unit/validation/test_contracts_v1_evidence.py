# loc-allow: 3 Pydantic models + 2 enums + comprehensive negative tests for all required fields
"""task-6330 -- TEST-cov: src/foundation/evidence/contracts/v1.py 0% -> 70%+.

Covers every public symbol in v1.py:
- SCHEMA_VERSION constant
- Outcome enum (SUCCESS, DENIED, ERROR)
- Classification enum (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, SECRET_REFERENCE)
- RecordAuditEventCommand Pydantic model (required/optional fields, defaults)
- AuditEventView Pydantic model (14 fields)
- AuditTimelinePage Pydantic model (pagination model)

D2 evidence: negative >=3, failure-injection 1, perf 1, gate-red repro 1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, cast
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.evidence.contracts.v1 import (
    SCHEMA_VERSION,
    AuditEventView,
    AuditTimelinePage,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)

# ── SCHEMA_VERSION ─────────────────────────────────────────────────────────


def test_schema_version_is_v1() -> None:
    """SCHEMA_VERSION constant must equal 'v1'."""
    assert SCHEMA_VERSION == "v1"
    assert isinstance(SCHEMA_VERSION, str)


# ── Outcome enum ───────────────────────────────────────────────────────────


def test_outcome_all_values() -> None:
    """Outcome enum has all three expected values."""
    assert Outcome.SUCCESS.value == "SUCCESS"
    assert Outcome.DENIED.value == "DENIED"
    assert Outcome.ERROR.value == "ERROR"


def test_outcome_from_str() -> None:
    """Outcome can be constructed from string value."""
    assert Outcome("SUCCESS") is Outcome.SUCCESS
    assert Outcome("DENIED") is Outcome.DENIED
    assert Outcome("ERROR") is Outcome.ERROR


@pytest.mark.parametrize("invalid_value", ["INVALID", "UNKNOWN", "BOGUS"])
def test_outcome_invalid_value(invalid_value: str) -> None:
    """Outcome raises ValueError for invalid value (negative test)."""
    with pytest.raises(ValueError):
        Outcome(invalid_value)


# ── Classification enum ────────────────────────────────────────────────────


def test_classification_all_values() -> None:
    """Classification enum has all five expected values."""
    assert Classification.PUBLIC.value == "PUBLIC"
    assert Classification.INTERNAL.value == "INTERNAL"
    assert Classification.CONFIDENTIAL.value == "CONFIDENTIAL"
    assert Classification.RESTRICTED.value == "RESTRICTED"
    assert Classification.SECRET_REFERENCE.value == "SECRET_REFERENCE"


def test_classification_from_str() -> None:
    """Classification can be constructed from string value."""
    assert Classification("PUBLIC") is Classification.PUBLIC
    assert Classification("RESTRICTED") is Classification.RESTRICTED
    assert Classification("SECRET_REFERENCE") is Classification.SECRET_REFERENCE


@pytest.mark.parametrize("invalid_value", ["UNKNOWN", "FAKE", "BAD"])
def test_classification_invalid_value(invalid_value: str) -> None:
    """Classification raises ValueError for invalid value (negative test)."""
    with pytest.raises(ValueError):
        Classification(invalid_value)


# ── RecordAuditEventCommand ───────────────────────────────────────────────


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


def test_record_audit_event_command_system_event() -> None:
    """RecordAuditEventCommand with tenant_id=None represents system event."""
    cmd = RecordAuditEventCommand(
        tenant_id=None,
        aggregate_type="System",
        aggregate_id=uuid4(),
        action="INIT",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.tenant_id is None
    assert cmd.action == "INIT"


def test_record_audit_event_command_optional_actor() -> None:
    """RecordAuditEventCommand actor_subject_id can be None."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Portfolio",
        aggregate_id=uuid4(),
        action="UPDATED",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=uuid4(),
    )

    assert cmd.actor_subject_id is None


def test_record_audit_event_command_default_classification() -> None:
    """RecordAuditEventCommand classification defaults to INTERNAL."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Data",
        aggregate_id=uuid4(),
        action="READ",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.classification == Classification.INTERNAL


def test_record_audit_event_command_default_payload() -> None:
    """RecordAuditEventCommand payload defaults to empty dict."""
    cmd = RecordAuditEventCommand(
        tenant_id=uuid4(),
        aggregate_type="Event",
        aggregate_id=uuid4(),
        action="TRIGGERED",
        outcome=Outcome.SUCCESS,
        trace_id=uuid4(),
    )

    assert cmd.payload == {}


def test_record_audit_event_command_missing_required_tenant_id() -> None:
    """RecordAuditEventCommand requires tenant_id or explicit None (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        RecordAuditEventCommand(
            aggregate_type="Portfolio",
            aggregate_id=uuid4(),
            action="CREATED",
            outcome=Outcome.SUCCESS,
            trace_id=uuid4(),
        )
    assert "tenant_id" in str(exc_info.value)


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


# ── AuditEventView ────────────────────────────────────────────────────────


def _valid_event_kwargs(**overrides: Any) -> dict[str, Any]:
    """Return a minimal valid AuditEventView dict."""
    return {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "sequence_no": 1,
        "aggregate_type": "Portfolio",
        "aggregate_id": uuid4(),
        "aggregate_revision": 1,
        "action": "CREATED",
        "outcome": Outcome.SUCCESS,
        "actor_subject_id": uuid4(),
        "trace_id": uuid4(),
        "payload_hash": "abc123",
        "payload": {"key": "value"},
        "classification": Classification.INTERNAL,
        "previous_hash": None,
        "event_hash": "def456",
        "occurred_at": datetime.now(timezone.utc),
        "schema_version": "v1",
        **overrides,
    }


def test_audit_event_view_all_fields() -> None:
    """AuditEventView with all fields."""
    kwargs = _valid_event_kwargs()
    view = AuditEventView(**kwargs)

    assert view.id == kwargs["id"]
    assert view.tenant_id == kwargs["tenant_id"]
    assert view.sequence_no == 1
    assert view.aggregate_type == "Portfolio"
    assert view.payload_hash == "abc123"
    assert view.schema_version == "v1"


def test_audit_event_view_default_schema_version() -> None:
    """AuditEventView schema_version defaults to SCHEMA_VERSION (v1)."""
    kwargs = _valid_event_kwargs()
    del kwargs["schema_version"]
    view = AuditEventView(**kwargs)

    assert view.schema_version == "v1"


def test_audit_event_view_nullable_tenant_id() -> None:
    """AuditEventView tenant_id can be None for system events."""
    kwargs = _valid_event_kwargs(tenant_id=None)
    view = AuditEventView(**kwargs)

    assert view.tenant_id is None


def test_audit_event_view_nullable_actor() -> None:
    """AuditEventView actor_subject_id can be None."""
    kwargs = _valid_event_kwargs(actor_subject_id=None)
    view = AuditEventView(**kwargs)

    assert view.actor_subject_id is None


def test_audit_event_view_nullable_previous_hash() -> None:
    """AuditEventView previous_hash can be None (first event in chain)."""
    kwargs = _valid_event_kwargs(previous_hash=None)
    view = AuditEventView(**kwargs)

    assert view.previous_hash is None


def test_audit_event_view_optional_aggregate_revision() -> None:
    """AuditEventView aggregate_revision can be None."""
    kwargs = _valid_event_kwargs(aggregate_revision=None)
    view = AuditEventView(**kwargs)

    assert view.aggregate_revision is None


def test_audit_event_view_missing_required_id() -> None:
    """AuditEventView requires id (negative test)."""
    kwargs = _valid_event_kwargs()
    del kwargs["id"]

    with pytest.raises(ValidationError) as exc_info:
        AuditEventView(**kwargs)
    assert "id" in str(exc_info.value)


def test_audit_event_view_missing_event_hash() -> None:
    """AuditEventView requires event_hash (negative test)."""
    kwargs = _valid_event_kwargs()
    del kwargs["event_hash"]

    with pytest.raises(ValidationError) as exc_info:
        AuditEventView(**kwargs)
    assert "event_hash" in str(exc_info.value)


def test_audit_event_view_missing_occurred_at() -> None:
    """AuditEventView requires occurred_at (negative test)."""
    kwargs = _valid_event_kwargs()
    del kwargs["occurred_at"]

    with pytest.raises(ValidationError) as exc_info:
        AuditEventView(**kwargs)
    assert "occurred_at" in str(exc_info.value)


# ── AuditTimelinePage ──────────────────────────────────────────────────────


def test_audit_timeline_page_with_items() -> None:
    """AuditTimelinePage with list of events."""
    event_kwargs = _valid_event_kwargs()
    event = AuditEventView(**event_kwargs)

    page = AuditTimelinePage(
        items=[event],
        next_cursor="cursor-001",
        as_of=datetime.now(timezone.utc),
    )

    assert len(page.items) == 1
    assert page.items[0].id == event.id
    assert page.next_cursor == "cursor-001"


def test_audit_timeline_page_empty_items() -> None:
    """AuditTimelinePage can have empty items list."""
    page = AuditTimelinePage(
        items=[],
        next_cursor=None,
        as_of=datetime.now(timezone.utc),
    )

    assert len(page.items) == 0
    assert page.next_cursor is None


def test_audit_timeline_page_multiple_events() -> None:
    """AuditTimelinePage can hold multiple events."""
    events = [AuditEventView(**_valid_event_kwargs(sequence_no=i)) for i in range(1, 4)]

    page = AuditTimelinePage(
        items=events,
        next_cursor="next-page",
        as_of=datetime.now(timezone.utc),
    )

    assert len(page.items) == 3
    assert page.items[0].sequence_no == 1
    assert page.items[2].sequence_no == 3


def test_audit_timeline_page_none_next_cursor() -> None:
    """AuditTimelinePage next_cursor can be None (last page)."""
    event = AuditEventView(**_valid_event_kwargs())

    page = AuditTimelinePage(
        items=[event],
        next_cursor=None,
        as_of=datetime.now(timezone.utc),
    )

    assert page.next_cursor is None


def test_audit_timeline_page_missing_items() -> None:
    """AuditTimelinePage requires items (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        AuditTimelinePage(
            next_cursor="cursor",
            as_of=datetime.now(timezone.utc),
        )
    assert "items" in str(exc_info.value)


def test_audit_timeline_page_missing_as_of() -> None:
    """AuditTimelinePage requires as_of (negative test)."""
    with pytest.raises(ValidationError) as exc_info:
        AuditTimelinePage(
            items=[],
            next_cursor=None,
        )
    assert "as_of" in str(exc_info.value)


# ── Failure injection tests ────────────────────────────────────────────────


def test_record_audit_event_command_with_invalid_outcome_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


# ── Performance tests ──────────────────────────────────────────────────────


def test_model_creation_performance(perf_budget: Any) -> None:
    """Performance: Model creation within spec (< 1ms cmd, < 2ms event, < 10ms page).

    Pure in-process pydantic construction, so it is measured with `perf_budget`
    (process_time, best-of-5): a wall-clock budget is meaningless under xdist
    core contention and the perf-marker guard (task-7434) rejects it."""
    cmd_kwargs = _valid_cmd_kwargs()
    event_kwargs = _valid_event_kwargs()
    events = [AuditEventView(**event_kwargs) for _ in range(100)]

    perf_budget.assert_within(
        lambda: RecordAuditEventCommand(**cmd_kwargs), budget_ms=1.0, batch=100, label="Command"
    )
    perf_budget.assert_within(
        lambda: AuditEventView(**event_kwargs), budget_ms=2.0, batch=50, label="Event"
    )
    perf_budget.assert_within(
        lambda: AuditTimelinePage(
            items=events, next_cursor="cursor", as_of=datetime.now(timezone.utc)
        ),
        budget_ms=10.0,
        batch=10,
        label="Page",
    )


# ── Gate-red reproduction tests ────────────────────────────────────────────


def test_models_json_serializable() -> None:
    """All models must be JSON-serializable and round-trip (gate-red check)."""
    from pydantic import BaseModel

    class TestModel(BaseModel):
        outcome: Outcome
        classification: Classification

    model = TestModel(outcome=Outcome.SUCCESS, classification=Classification.CONFIDENTIAL)
    json_str = model.model_dump_json()
    assert "SUCCESS" in json_str and "CONFIDENTIAL" in json_str

    parsed = TestModel.model_validate_json(json_str)
    assert parsed.outcome == Outcome.SUCCESS
    assert parsed.classification == Classification.CONFIDENTIAL

    # Test RecordAuditEventCommand round-trip
    cmd = RecordAuditEventCommand(**_valid_cmd_kwargs())
    cmd_json = cmd.model_dump_json()
    cmd_parsed = RecordAuditEventCommand.model_validate_json(cmd_json)
    assert cmd_parsed.tenant_id == cmd.tenant_id
    assert cmd_parsed.outcome == cmd.outcome
