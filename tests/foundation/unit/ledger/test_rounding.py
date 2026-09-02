"""LC-2 — rounding 단위테스트: 합 보존 반올림.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §9 LC-2
(테스트 목록 §9: "split_commission(10001, 0.15) 합 = 10001 정확,
HALF_EVEN(0.005→0.00, 0.015→0.02)").
"""
from decimal import Decimal

import pytest

from src.foundation.ledger.domain.rounding import split_commission


def test_split_commission_sum_matches_price_exactly() -> None:
    price = Decimal("10001")

    commission, payout = split_commission(price, Decimal("0.15"))

    assert commission + payout == price
    assert commission == Decimal("1500.15")
    assert payout == Decimal("8500.85")


@pytest.mark.parametrize(
    "price, rate",
    [
        (Decimal("100.00"), Decimal("0.15")),
        (Decimal("73.33"), Decimal("0.15")),
        (Decimal("1"), Decimal("0.15")),
        (Decimal("0.01"), Decimal("0.99")),
        (Decimal("999999999.99"), Decimal("0.13")),
    ],
)
def test_split_commission_never_leaves_a_residual(price: Decimal, rate: Decimal) -> None:
    """분배 후 잔차가 1원(0.01)이라도 남으면 이 assert가 실패해야 한다 — 근사 비교 금지."""
    commission, payout = split_commission(price, rate)

    assert commission + payout == price


def test_split_commission_rounds_half_to_even_down() -> None:
    # price=0.05, rate=0.10 -> price*rate = 0.005 -> HALF_EVEN: 0.00(짝수)로.
    commission, payout = split_commission(Decimal("0.05"), Decimal("0.10"))

    assert commission == Decimal("0.00")
    assert payout == Decimal("0.05")


def test_split_commission_rounds_half_to_even_up() -> None:
    # price=0.10, rate=0.15 -> price*rate = 0.015 -> HALF_EVEN: 0.02(짝수)로.
    commission, payout = split_commission(Decimal("0.10"), Decimal("0.15"))

    assert commission == Decimal("0.02")
    assert payout == Decimal("0.08")


def test_split_commission_returns_two_decimal_places() -> None:
    commission, payout = split_commission(Decimal("100.00"), Decimal("0.15"))

    assert commission.as_tuple().exponent == -2
    assert commission == Decimal("15.00")
    assert payout == Decimal("85.00")


def test_split_commission_rejects_non_decimal_residual_via_exact_equality() -> None:
    """반올림 잔차가 조금이라도 남으면(예: 구현이 payout을 독립적으로 반올림하면)
    아래 exact equality가 실패해야 한다 — 근사(pytest.approx) 사용 금지가 DoD."""
    price = Decimal("33.33")

    commission, payout = split_commission(price, Decimal("0.15"))

    assert commission + payout == price
