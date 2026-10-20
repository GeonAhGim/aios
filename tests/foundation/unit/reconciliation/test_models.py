"""Reconciliation & Resilience domain/models.py unit tests — pure value objects, no DB.

Covers construction, frozen immutability, enum validation, and equality for
ReconciliationItem/ReconciliationRun/ReconciliationState/MaterialityPolicy and
their state enums.
"""

import dataclasses
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest

from src.foundation.reconciliation.domain.models import (
    Classification,
    MaterialityPolicy,
    ReconciliationItem,
    ReconciliationRun,
    ReconciliationState,
    RunState,
)
from tests.support.perf_budget import PerfBudget

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _policy(**overrides: Any) -> MaterialityPolicy:
    fields: dict[str, Any] = {
        "absolute_tolerance": Decimal("0.01"),
        "relative_tolerance_pct": Decimal("0.1"),
    }
    fields.update(overrides)
    return cast(MaterialityPolicy, MaterialityPolicy(**cast(Any, fields)))


def _item(**overrides: Any) -> ReconciliationItem:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "run_id": uuid4(),
        "entity_type": "USDT_BALANCE",
        "entity_key": "acct-1",
        "internal_value": Decimal("100.00"),
        "provider_value": Decimal("100.00"),
        "classification": Classification.HEALTHY,
        "created_at": NOW,
    }
    fields.update(overrides)
    return cast(ReconciliationItem, ReconciliationItem(**cast(Any, fields)))


def _run(**overrides: Any) -> ReconciliationRun:
    fields: dict[str, Any] = {
        "id": uuid4(),
        "tenant_id": uuid4(),
        "target_type": "ACCOUNT",
        "target_ref": uuid4(),
        "connection_id": uuid4(),
        "input_hash": "hash",
        "state": RunState.COMPLETED,
        "rule_version": "v1",
        "items": (),
        "created_at": NOW,
    }
    fields.update(overrides)
    return cast(ReconciliationRun, ReconciliationRun(**cast(Any, fields)))


def _state(**overrides: Any) -> ReconciliationState:
    fields: dict[str, Any] = {
        "target_ref": uuid4(),
        "target_type": "ACCOUNT",
        "tenant_id": uuid4(),
        "aggregate_status": Classification.HEALTHY,
        "last_healthy_at": NOW,
        "last_checked_at": NOW,
        "blocking_reason": None,
        "revision": 1,
        "safety_control_id": None,
        "resolved_by": None,
        "resolution_reason": None,
        "resolved_at": None,
    }
    fields.update(overrides)
    return cast(ReconciliationState, ReconciliationState(**cast(Any, fields)))


# --- positive construction ---------------------------------------------------


def test_materiality_policy_constructs_with_expected_fields() -> None:
    p = _policy()
    assert p.absolute_tolerance == Decimal("0.01")
    assert p.relative_tolerance_pct == Decimal("0.1")


def test_reconciliation_item_constructs_with_expected_fields() -> None:
    item = _item()
    assert item.classification is Classification.HEALTHY
    assert item.provider_value == Decimal("100.00")


def test_reconciliation_run_constructs_with_expected_fields() -> None:
    run = _run()
    assert run.state is RunState.COMPLETED
    assert run.items == ()


def test_reconciliation_state_constructs_with_expected_fields() -> None:
    state = _state()
    assert state.aggregate_status is Classification.HEALTHY
    assert state.blocking_reason is None


def test_reconciliation_item_allows_none_provider_value() -> None:
    """REC-003 — a missing provider value is a valid state, not an error."""
    item = _item(provider_value=None, classification=Classification.PROVIDER_UNAVAILABLE)
    assert item.provider_value is None


def test_reconciliation_run_carries_nested_items() -> None:
    item = _item()
    run = _run(items=(item,))
    assert run.items == (item,)


def test_dataclasses_with_equal_fields_compare_equal() -> None:
    shared_id = uuid4()
    a = _policy()
    b = _policy()
    assert a == b
    shared_run_id = uuid4()
    assert _item(id=shared_id, run_id=shared_run_id) == _item(id=shared_id, run_id=shared_run_id)


# --- negative: frozen immutability -------------------------------------------


def test_materiality_policy_is_frozen_and_rejects_mutation() -> None:
    p = _policy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(p, "absolute_tolerance", Decimal("1"))  # noqa: B010


def test_reconciliation_item_is_frozen_and_rejects_mutation() -> None:
    item = _item()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(item, "classification", Classification.MATERIAL_MISMATCH)  # noqa: B010


def test_reconciliation_run_is_frozen_and_rejects_mutation() -> None:
    run = _run()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(run, "state", RunState.DEDUPED)  # noqa: B010


def test_reconciliation_state_is_frozen_and_rejects_mutation() -> None:
    state = _state()
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(state, "revision", 2)  # noqa: B010


# --- negative: invalid construction ------------------------------------------


def test_reconciliation_item_missing_required_field_raises_type_error() -> None:
    with pytest.raises(TypeError):
        cast(
            Any,
            ReconciliationItem(
                id=uuid4(),
                run_id=uuid4(),
                entity_type="USDT_BALANCE",
                entity_key="acct-1",
                internal_value=Decimal("100.00"),
                # provider_value omitted, classification omitted
            ),
        )


def test_reconciliation_run_missing_required_field_raises_type_error() -> None:
    with pytest.raises(TypeError):
        cast(
            Any,
            ReconciliationRun(
                id=uuid4(),
                tenant_id=uuid4(),
                target_type="ACCOUNT",
                # target_ref, connection_id, input_hash, state, rule_version omitted
            ),
        )


def test_classification_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        Classification("UNKNOWN")


def test_run_state_rejects_unknown_value() -> None:
    with pytest.raises(ValueError):
        RunState("PENDING")


# --- failure injection --------------------------------------------------------


def test_classification_construction_surfaces_injected_lookup_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Simulate a broken enum value-lookup dependency (e.g. corrupted _value2member_map_)
    to verify enum construction fails closed rather than silently returning a bad member."""
    broken_map: dict[object, object] = {}
    monkeypatch.setattr(Classification, "_value2member_map_", broken_map)

    with pytest.raises(ValueError):
        Classification("HEALTHY")


# --- performance ---------------------------------------------------------------


@pytest.mark.perf
def test_bulk_construction_meets_latency_budget(perf_budget: Any) -> None:
    """10k instantiations of every value object must stay well under 1s (p50 budget
    for pure in-memory dataclass construction, ADR-2026-09-09-C Decision 1 default)."""

    def _bulk() -> None:
        for _ in range(10_000):
            _policy()
            _item()
            _run()
            _state()

    perf_budget.assert_within(_bulk, budget_ms=1000.0, n=1)


@pytest.mark.perf
def test_perf_assertion_fails_with_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인한다 — `time.process_time`을
    monkeypatch해 1회 호출당 예산(1000ms)을 10배 초과하는 CPU 시간을 가장하면
    `assert_within`가 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 12000.0  # 12s per call → budget 1000ms × 12x 초과
        return _fake_time

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    def _bulk() -> None:
        for _ in range(10_000):
            _policy()
            _item()
            _run()
            _state()

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _bulk, budget_ms=1000.0, n=1, label="reconciliation bulk delay-injected"
        )


def test_single_call_cpu_converges_to_sub_tick_value() -> None:
    """단일 호출 CPU 시간이 양자화 오차 범위 내에 수렴하는지 검증(task-11539 감사 후속).

    `_policy()` 등 value object construction은 O(1) 인메모리 작업이므로
    `time.process_time()`으로 측정한 CPU 시간은 ~0ms여야 한다.
    Windows `time.process_time()`은 ~15.6ms 양자화 오차가 있다 —
    batch=64로 묶으면 오차가 ~0.24ms로 줄어든다. 64회 평균 CPU 시간이
    1ms 미만이면 충분히 0에 수렴한다고 판단한다.
    """
    pb = PerfBudget()

    # batch=1: 단일 호출 — 양자화 영향 큼
    samples_single = [pb.sample(_policy).cpu_ms for _ in range(64)]

    # batch=64: 64회 묶음 — 양자화 오차 1/64로 줄어듦
    sample_batch = pb.sample(_policy, batch=64)

    avg_single = sum(samples_single) / len(samples_single)
    assert avg_single < 1.0, f"single-call avg cpu_ms={avg_single:.3f}ms가 1ms 미만이 아님"
    assert sample_batch.cpu_ms < 1.0, (
        f"batch=64 cpu_ms={sample_batch.cpu_ms:.3f}ms가 1ms 미만이 아님"
    )


def test_bulk_construction_negative_empty_policy_raises() -> None:
    """MaterialityPolicy에 필수 필드가 없으면 construction이 실패한다."""
    with pytest.raises(TypeError):
        MaterialityPolicy()


def test_bulk_construction_negative_missing_required_item_field_raises() -> None:
    """ReconciliationItem에 필수 필드(entity_type)가 없으면 construction이 실패한다."""
    with pytest.raises(TypeError):
        ReconciliationItem(
            id=uuid4(),
            run_id=uuid4(),
            provider_value=Decimal("100.00"),
            created_at=NOW,
        )


def test_bulk_construction_negative_invalid_classification_raises() -> None:
    """잘못된 Classification 값은 construction을 차단한다."""
    with pytest.raises(ValueError):
        ReconciliationItem(
            id=uuid4(),
            run_id=uuid4(),
            entity_type="USDT_BALANCE",
            entity_key="acct-1",
            internal_value=Decimal("100.00"),
            provider_value=Decimal("100.00"),
            classification=Classification("INVALID"),
            created_at=NOW,
        )
