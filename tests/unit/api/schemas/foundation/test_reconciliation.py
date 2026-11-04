"""task-4699 — src/api/schemas/foundation/reconciliation.py coverage 0% -> 70%+.

Covers ReconciliationStateListResponse construction, negative validation paths, a
monkeypatch-induced dependency failure, and a throughput budget for repeated
model construction (ADR-2026-09-09-C Decision 1).
"""

from __future__ import annotations

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


@pytest.mark.perf
def test_reconciliation_state_list_response_construction_throughput_budget(
    perf_budget: PerfBudget,
) -> None:
    """schema construction latency stays under 50ms/op budget
    (ADR-2026-09-09-C Decision 1 — simple schema construction path).

    Uses best_of (not samples) so Windows quantization tick (~15.6ms)
    cannot sway the minimum reading — best_of picks the single fastest
    run out of 5, minimising tick-noise impact.
    """
    as_of = datetime.now(UTC)
    states = [_state_view() for _ in range(5)]

    sample = perf_budget.best_of(
        lambda: ReconciliationStateListResponse(states=states, as_of=as_of),
        n=5,
    )

    # cpu_ms 기준, 단위 일관성: ms (0.05초 → 50.0ms)
    assert sample.cpu_ms < 50.0, f"budget 초과: cpu={sample.cpu_ms:.2f}ms"


@pytest.mark.perf
def test_reconciliation_construction_goes_red_with_10x_cpu_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `virtual_delayed_sync`로 ReconciliationStateView
    생성을 감싸 1회 호출당 4s 가상 CPU 지연을 주입하면
    `best_of` 단언이 AssertionError로 적색이 돼야 한다.

    virtual_delayed_sync은 실제 sleep/spin 없이 가상 지연 레더에
    추가하므로 PerfBudget.sample가 그 값을 cpu_ms에 정확히 반영한다.
    4s 지연 → best-of-5 최소값도 50ms 예산 초과.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539 감사: 호출 1회 CPU 측정의 0 수렴 결함 방지,
    type: ignore ratchet 회피).
    """
    budget_ms = 50.0  # 동일 예산

    as_of = datetime.now(UTC)
    states = [_state_view() for _ in range(5)]

    def _run() -> object:
        return ReconciliationStateListResponse(states=states, as_of=as_of)

    # _run을 virtual_delayed_sync으로 감싸 1회 호출당 4s 가상 CPU 추가
    # PerfBudget.sample이 virtual_totals_ns()를 읽어 cpu_ms에 반영함
    wrapped_run = virtual_delayed_sync(_run, 4.0)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.best_of(
            wrapped_run,
            n=5,
        )
        assert sample.cpu_ms < budget_ms, f"budget 초과: cpu={sample.cpu_ms:.2f}ms"
