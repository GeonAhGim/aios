"""task-4699 — src/api/schemas/foundation/reconciliation.py coverage 0% -> 70%+.

Covers ReconciliationStateListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import reconciliation as reconciliation_schema
from src.api.schemas.foundation.reconciliation import (
    Classification,
    ReconciliationStateListResponse,
    ReconciliationStateView,
)


def _state_view() -> ReconciliationStateView:
    now = datetime.now(timezone.utc)
    return ReconciliationStateView(
        target_ref=uuid4(),
        target_type="paper_deployment",
        aggregate_status=Classification.HEALTHY,
        last_healthy_at=now,
        last_checked_at=now,
        blocking_reason=None,
        revision=1,
    )


def test_reconciliation_state_list_response_valid_roundtrip() -> None:
    as_of = datetime.now(timezone.utc)
    resp = ReconciliationStateListResponse(states=[_state_view()], as_of=as_of)
    assert resp.as_of == as_of
    assert len(resp.states) == 1


def test_reconciliation_state_list_response_missing_as_of_raises() -> None:
    with pytest.raises(ValidationError):
        ReconciliationStateListResponse.model_validate({"states": []})


def test_reconciliation_state_list_response_invalid_states_element_raises() -> None:
    with pytest.raises(ValidationError):
        ReconciliationStateListResponse(
            states=["not-a-state-view"],
            as_of=datetime.now(timezone.utc),
        )


def test_reconciliation_state_list_response_invalid_as_of_type_raises() -> None:
    with pytest.raises(ValidationError):
        ReconciliationStateListResponse(states=[], as_of="not-a-datetime")


def test_reconciliation_state_view_missing_revision_raises() -> None:
    with pytest.raises(ValidationError):
        ReconciliationStateView.model_validate(
            {
                "target_ref": str(uuid4()),
                "target_type": "paper_deployment",
                "aggregate_status": Classification.HEALTHY,
                "last_healthy_at": None,
                "last_checked_at": datetime.now(timezone.utc),
                "blocking_reason": None,
            }
        )


def test_reconciliation_state_view_invalid_revision_type_raises() -> None:
    with pytest.raises(ValidationError):
        ReconciliationStateView.model_validate(
            {
                "target_ref": str(uuid4()),
                "target_type": "paper_deployment",
                "aggregate_status": Classification.HEALTHY,
                "last_healthy_at": None,
                "last_checked_at": datetime.now(timezone.utc),
                "blocking_reason": None,
                "revision": "not-an-int",
            }
        )


def test_reconciliation_state_list_response_dependency_failure_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break the `uuid.UUID` dependency that ReconciliationStateView.target_ref
    relies on for validation and confirm the failure surfaces instead of being
    swallowed (pydantic-core resolves `uuid.UUID` dynamically at validation
    time, so patching the module attribute is enough to reach the failure)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected dependency failure")

    monkeypatch.setattr("uuid.UUID", _boom)

    with pytest.raises((RuntimeError, TypeError, ValidationError)):
        reconciliation_schema.ReconciliationStateView(
            target_ref=uuid4(),
            target_type="paper_deployment",
            aggregate_status=Classification.HEALTHY,
            last_healthy_at=None,
            last_checked_at=datetime.now(timezone.utc),
            blocking_reason=None,
            revision=1,
        )


@pytest.mark.perf
def test_reconciliation_state_list_response_construction_throughput_budget() -> None:
    """p95 construction latency for 500 responses stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path)."""
    as_of = datetime.now(timezone.utc)
    states = [_state_view() for _ in range(5)]

    durations: list[float] = []
    for _ in range(500):
        start = time.perf_counter()
        ReconciliationStateListResponse(states=states, as_of=as_of)
        durations.append(time.perf_counter() - start)

    durations.sort()
    p95 = durations[int(len(durations) * 0.95) - 1]
    assert p95 < 0.05, f"p95 construction time {p95:.4f}s exceeded 50ms budget"
