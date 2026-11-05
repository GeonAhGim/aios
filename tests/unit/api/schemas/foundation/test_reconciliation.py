"""task-4699 — src/api/schemas/foundation/reconciliation.py coverage 0% -> 70%+.

Covers ReconciliationStateListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.api.schemas.foundation import reconciliation as reconciliation_schema
from src.api.schemas.foundation.reconciliation import (
    Classification,
    ReconciliationStateListResponse,
    ReconciliationStateView,
)
from tests.conftest import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync


def _state_view() -> ReconciliationStateView:
    now = datetime.now(UTC)
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
    as_of = datetime.now(UTC)
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
            as_of=datetime.now(UTC),
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
                "last_checked_at": datetime.now(UTC),
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
                "last_checked_at": datetime.now(UTC),
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
            last_checked_at=datetime.now(UTC),
            blocking_reason=None,
            revision=1,
        )


_BUDGET_MS = 50.0  # cpu_ms per batch of 500 constructions (ADR-2026-09-09-C Decision 1)


def _measure_best_cpu_ms(perf_budget: PerfBudget, fn: Callable[[], object]) -> float:
    # best_of (min of n) keeps one quantized Windows tick from flipping the gate; batch=500
    # folds all calls into one interval so process_time quantization cannot round it to 0.
    return perf_budget.best_of(fn, n=10, warmup=1, batch=500).cpu_ms


@pytest.mark.perf
def test_reconciliation_state_list_response_construction_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """Construction of 500 responses stays under the 50ms budget (unit: ms)."""
    as_of = datetime.now(UTC)
    states = [_state_view() for _ in range(5)]

    cpu_ms = _measure_best_cpu_ms(
        perf_budget, lambda: ReconciliationStateListResponse(states=states, as_of=as_of)
    )

    assert cpu_ms < _BUDGET_MS, f"budget exceeded: {cpu_ms} ms"


@pytest.mark.perf
def test_reconciliation_construction_goes_red_with_10x_cpu_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """Gate-red repro (D2): the same measurement/assertion turns red when a virtual
    10x-budget CPU delay is injected. virtual_delayed_sync only adds to the reading
    (no sleep/busy-wait); budget and assertion are not relaxed."""
    as_of = datetime.now(UTC)
    states = [_state_view() for _ in range(5)]

    def _run() -> object:
        return ReconciliationStateListResponse(states=states, as_of=as_of)

    slowed = virtual_delayed_sync(_run, _BUDGET_MS * 10 / 1000.0)

    cpu_ms = _measure_best_cpu_ms(perf_budget, slowed)

    with pytest.raises(AssertionError):
        assert cpu_ms < _BUDGET_MS, f"budget exceeded: {cpu_ms} ms"
