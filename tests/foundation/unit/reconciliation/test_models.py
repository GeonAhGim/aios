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

NOW = datetime(2026, 9, 24, tzinfo=timezone.utc)


def _policy(**overrides: Any) -> MaterialityPolicy:
    fields: dict[str, Any] = {"absolute_tolerance": Decimal("0.01"), "relative_tolerance_pct": Decimal("0.1")}
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
        cast(Any, ReconciliationItem(
            id=uuid4(),
            run_id=uuid4(),
            entity_type="USDT_BALANCE",
            entity_key="acct-1",
            internal_value=Decimal("100.00"),
            # provider_value omitted, classification omitted
        ))


def test_reconciliation_run_missing_required_field_raises_type_error() -> None:
    with pytest.raises(TypeError):
        cast(Any, ReconciliationRun(
            id=uuid4(),
            tenant_id=uuid4(),
            target_type="ACCOUNT",
            # target_ref, connection_id, input_hash, state, rule_version omitted
        ))


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


def test_bulk_construction_meets_latency_budget() -> None:
    """10k instantiations of every value object must stay well under 1s (p50 budget
    for pure in-memory dataclass construction, ADR-2026-09-09-C Decision 1 default)."""
    start = time.perf_counter()
    for _ in range(10_000):
        _policy()
        _item()
        _run()
        _state()
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
