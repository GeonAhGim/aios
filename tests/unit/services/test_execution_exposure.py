"""LP-4 순수 함수: 단계 자본과 손실 가드 환산."""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.services.execution_exposure import loss_guard_pct, staged_capital
from src.services.execution_types import ExecutionControlError


@pytest.mark.parametrize(
    ("target", "pct", "expected"),
    [
        ("500", "10", "50"),
        ("500", "100", "500"),
        ("500", "1", "5"),
        ("1000", "33.33", "333.3"),
        ("100", "0.01", "0.01"),
    ],
)
def test_staged_capital_is_the_percentage_of_the_target(target, pct, expected) -> None:
    assert staged_capital(Decimal(target), Decimal(pct)) == Decimal(expected)


def test_staged_capital_rounds_down_never_opening_more_than_approved() -> None:
    # 10 * 33.3333333333% = 3.33333333330 -- 반올림이면 위로 올라갈 수 있는 자리에서 내림이어야 한다
    value = staged_capital(Decimal("10"), Decimal("33.33333333339"))
    assert value <= Decimal("10") * Decimal("33.33333333339") / Decimal(100)


def test_loss_guard_converts_amount_to_percent_of_current_capital() -> None:
    assert loss_guard_pct(Decimal("10"), Decimal("100")) == Decimal("10.00")
    assert loss_guard_pct(Decimal("25"), Decimal("200")) == Decimal("12.50")


def test_loss_guard_rounds_down_so_the_guard_trips_before_the_limit() -> None:
    # 1/3 = 33.333...% -> 33.33 (내림) -- 33.34로 올리면 한도보다 늦게 걸린다
    assert loss_guard_pct(Decimal("1"), Decimal("3")) == Decimal("33.33")


def test_loss_guard_is_capped_at_100_percent_when_limit_exceeds_capital() -> None:
    # 초기 노출이 작아 자본(50)이 손실 한도(100)보다 작으면 전액(100%)으로 자른다
    assert loss_guard_pct(Decimal("100"), Decimal("50")) == Decimal(100)


def test_loss_guard_never_drops_below_the_column_minimum() -> None:
    assert loss_guard_pct(Decimal("0.0001"), Decimal("1000000")) == Decimal("0.01")


@pytest.mark.parametrize("capital", [Decimal(0), Decimal(-5)])
def test_loss_guard_rejects_non_positive_capital(capital) -> None:
    with pytest.raises(ExecutionControlError):
        loss_guard_pct(Decimal(10), capital)
