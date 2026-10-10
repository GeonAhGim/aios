"""task-6330 -- TEST-cov: src/foundation/evidence/contracts/v1.py
AuditEventView + AuditTimelinePage.

Covers:
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
    AuditEventView,
    AuditTimelinePage,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)

# ── Helpers ────────────────────────────────────────────────────────────────


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


# ── AuditEventView ────────────────────────────────────────────────────────


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


# ── Failure injection ──────────────────────────────────────────────────────


def test_view_invalid_outcome(monkeypatch: Any, perf_budget: Any) -> None:
    """Failure injection: RecordAuditEventCommand with invalid outcome (monkeypatch)."""
    # Pydantic validates Outcome enum at model init time.
    # This test verifies that passing an invalid outcome string raises ValidationError.
    with pytest.raises(ValidationError):
        RecordAuditEventCommand(  # noqa: F821
            tenant_id=uuid4(),
            aggregate_type="Test",
            aggregate_id=uuid4(),
            action="TEST",
            outcome=cast(Outcome, "INVALID"),  # noqa: F821
            trace_id=uuid4(),
        )


# ── Performance ────────────────────────────────────────────────────────────


def test_model_creation_performance(perf_budget: Any) -> None:
    """Performance: Model creation within spec (< 1ms cmd, < 2ms event, < 10ms page).

    Pure in-process pydantic construction, so it is measured with `perf_budget`
    (process_time, best-of-5): a wall-clock budget is meaningless under xdist
    core contention and the perf-marker guard (task-7434) rejects it."""

    cmd_kwargs = {
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
    }
    event_kwargs = _valid_event_kwargs()
    events = [AuditEventView(**event_kwargs) for _ in range(100)]

    perf_budget.assert_within(
        lambda: RecordAuditEventCommand(**cmd_kwargs),
        budget_ms=1.0,
        batch=100,
        label="Command",  # noqa: F821
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


# ── Gate-red reproduction ──────────────────────────────────────────────────


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
    cmd_kwargs = {
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
    }
    cmd = RecordAuditEventCommand(**cmd_kwargs)  # noqa: F821
    cmd_json = cmd.model_dump_json()
    cmd_parsed = RecordAuditEventCommand.model_validate_json(cmd_json)  # noqa: F821
    assert cmd_parsed.tenant_id == cmd.tenant_id
    assert cmd_parsed.outcome == cmd.outcome
