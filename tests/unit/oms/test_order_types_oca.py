"""OCA(one-cancels-all) 브래킷 청산 단위테스트 — EM-19, task-2623.

DoD: OCA N레그 조정이 원자적(1000회 적대 실행에서 이중 체결 0), 부분체결
시 브래킷 청산 수량 정합(bracket_quantity_for_fill)."""

from __future__ import annotations

import threading
from decimal import Decimal

import pytest

from src.services.oms.domain.order_types.oca import (
    OcaGroup,
    OcaResolution,
    bracket_quantity_for_fill,
    resolve_oca,
)
from src.services.oms.domain.order_types.oco import OcoOutcome


def test_resolve_oca_triggers_profit_leg_only() -> None:
    result = resolve_oca(
        triggered={"profit": True, "loss": False, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result == OcaResolution(triggered_leg="profit", cancelled_legs=("loss", "trail"))


def test_resolve_oca_no_leg_triggered() -> None:
    result = resolve_oca(
        triggered={"profit": False, "loss": False, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result == OcaResolution(triggered_leg=None, cancelled_legs=())


def test_resolve_oca_same_tick_ambiguity_uses_priority_order() -> None:
    result = resolve_oca(
        triggered={"profit": True, "loss": True, "trail": False},
        priority_order=("loss", "profit", "trail"),
    )
    assert result.triggered_leg == "loss"
    assert set(result.cancelled_legs) == {"profit", "trail"}


def test_resolve_oca_rejects_empty_priority_order() -> None:
    with pytest.raises(ValueError, match="priority_order"):
        resolve_oca(triggered={}, priority_order=())


def test_resolve_oca_rejects_mismatched_leg_sets() -> None:
    with pytest.raises(ValueError, match="triggered"):
        resolve_oca(triggered={"profit": True}, priority_order=("profit", "loss"))


def test_oca_group_first_leg_wins_and_siblings_are_cancelled() -> None:
    group = OcaGroup(legs=frozenset({"profit", "loss", "trail"}))
    assert group.try_trigger("loss") is OcoOutcome.TRIGGERED
    assert group.try_trigger("profit") is OcoOutcome.CANCELLED
    assert group.try_trigger("trail") is OcoOutcome.CANCELLED
    assert group.winner == "loss"


def test_oca_group_repeated_calls_are_idempotent() -> None:
    group = OcaGroup(legs=frozenset({"profit", "loss"}))
    assert group.try_trigger("profit") is OcoOutcome.TRIGGERED
    assert group.try_trigger("profit") is OcoOutcome.TRIGGERED
    assert group.try_trigger("loss") is OcoOutcome.CANCELLED
    assert group.try_trigger("loss") is OcoOutcome.CANCELLED


def test_oca_group_rejects_unknown_leg() -> None:
    group = OcaGroup(legs=frozenset({"profit", "loss"}))
    with pytest.raises(ValueError, match="unknown leg"):
        group.try_trigger("trail")


def test_oca_group_atomic_under_1000_adversarial_concurrent_triggers() -> None:
    """3레그(profit/loss/trail) 각각 다수의 스레드로 동시에 `try_trigger`를
    호출해도 TRIGGERED는 정확히 한 레그에서만 나와야 한다."""
    legs = ("profit", "loss", "trail")
    group = OcaGroup(legs=frozenset(legs))
    outcomes: dict[str, list[OcoOutcome]] = {leg: [] for leg in legs}
    lock = threading.Lock()

    def _attempt(leg: str) -> None:
        outcome = group.try_trigger(leg)
        with lock:
            outcomes[leg].append(outcome)

    threads = [threading.Thread(target=_attempt, args=(leg,)) for _ in range(340) for leg in legs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    triggered_counts = {leg: outcomes[leg].count(OcoOutcome.TRIGGERED) for leg in legs}
    assert sum(triggered_counts.values()) == 340
    assert sorted(triggered_counts.values()) == [0, 0, 340]
    assert group.winner in legs


# ---- bracket_quantity_for_fill (부분체결 수량 정합) ----


def test_bracket_quantity_for_fill_clamps_to_partial_entry_fill() -> None:
    result = bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("4"))
    assert result == Decimal("4")


def test_bracket_quantity_for_fill_full_fill_returns_requested_qty() -> None:
    result = bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("10"))
    assert result == Decimal("10")


def test_bracket_quantity_for_fill_rejects_negative_filled_qty() -> None:
    with pytest.raises(ValueError, match="filled_qty"):
        bracket_quantity_for_fill(requested_qty=Decimal("10"), filled_qty=Decimal("-1"))


def test_bracket_quantity_for_fill_rejects_nan_requested_qty() -> None:
    with pytest.raises(ValueError, match="requested_qty"):
        bracket_quantity_for_fill(requested_qty=Decimal("NaN"), filled_qty=Decimal("1"))
