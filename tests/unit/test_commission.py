"""13.7 단위테스트 — 순수 계산 로직."""

from decimal import Decimal, InvalidOperation
from unittest.mock import patch

import pytest

from src.services.commission import (
    CommissionError,
    calculate_commission,
)


def test_calculates_commission_and_payout_at_default_rate():
    commission, payout = calculate_commission(Decimal("100.00"))

    assert commission == Decimal("15.0000")
    assert payout == Decimal("85.0000")


def test_calculates_at_custom_rate():
    commission, payout = calculate_commission(Decimal("200.00"), rate=Decimal("0.10"))

    assert commission == Decimal("20.0000")
    assert payout == Decimal("180.0000")


def test_none_price_paid_returns_none_none():
    commission, payout = calculate_commission(None)

    assert commission is None
    assert payout is None


def test_commission_plus_payout_equals_price_paid():
    price = Decimal("73.33")
    commission, payout = calculate_commission(price, rate=Decimal("0.15"))

    assert commission + payout == price


def test_commission_delegates_rounding_to_domain_rounding_module():
    """LC-2: price × rate가 정확히 2dp로 안 떨어지는 경우 domain/rounding의
    ROUND_HALF_EVEN 규칙이 적용되고(§3.3), commission + payout == price가 유지된다."""
    price = Decimal("0.10")

    commission, payout = calculate_commission(price, rate=Decimal("0.15"))

    assert commission == Decimal("0.02")  # 0.10*0.15=0.015 -> HALF_EVEN -> 0.02
    assert payout == Decimal("0.08")
    assert commission + payout == price


# ── Negative tests (LC-2: invalid inputs must be rejected) ──────────────


def test_negative_price_raises_commission_error():
    """LC-2: 음수 가격은 CommissionError를 던져야 한다 — 수수료는
    양수 거래 금액에만 적용된다."""
    with pytest.raises(CommissionError, match="Price must be non-negative"):
        calculate_commission(Decimal("-100.00"))


def test_rate_greater_than_one_raises_commission_error():
    """LC-2: 수수료율(rate)은 0~1 사이 값이어야 한다. 1을 초과하면
    판매자 정산액이 음수가 되어 원장 불변식 위반."""
    with pytest.raises(CommissionError, match="Rate must be in \\[0, 1\\]"):
        calculate_commission(Decimal("100.00"), rate=Decimal("1.5"))


def test_rate_negative_raises_commission_error():
    """LC-2: 음수 수수료율은 의미없다 — 거부해야 한다."""
    with pytest.raises(CommissionError, match="Rate must be in \\[0, 1\\]"):
        calculate_commission(Decimal("100.00"), rate=Decimal("-0.10"))


def test_rate_nan_raises_commission_error():
    """LC-2: NaN(rate)은 비유한 값이므로 CommissionError를 던져야 한다."""
    with pytest.raises(CommissionError, match="Rate must be finite"):
        calculate_commission(Decimal("100.00"), rate=Decimal("nan"))


def test_zero_price_returns_zero_zero():
    """LC-2: 가격 0원은 유효한 경계값 — (0.00, 0.00)을 반환해야 한다."""
    commission, payout = calculate_commission(Decimal("0.00"))

    assert commission == Decimal("0.00")
    assert payout == Decimal("0.00")
    assert commission + payout == Decimal("0.00")


def test_rate_exactly_one_returns_full_as_commission():
    """LC-2: rate=1.0은 경계값 — 전체 금액이 수수료, 정산액 0."""
    commission, payout = calculate_commission(Decimal("100.00"), rate=Decimal("1.0"))

    assert commission == Decimal("100.00")
    assert payout == Decimal("0.00")
    assert commission + payout == Decimal("100.00")


def test_rate_zero_returns_zero_commission():
    """LC-2: rate=0.0은 경계값 — 수수료 0, 정산액 전액."""
    commission, payout = calculate_commission(Decimal("100.00"), rate=Decimal("0.0"))

    assert commission == Decimal("0.00")
    assert payout == Decimal("100.00")
    assert commission + payout == Decimal("100.00")


# ── Failure-injection test ──────────────────────────────────────────────


def test_split_commission_exception_propagates_as_commission_error():
    """실패주입: domain/rounding.split_commission이 예외를 던지면
    calculate_commission도 실패해야 한다 — 의존성 실패 시 fail-closed."""
    with patch(
        "src.services.commission.split_commission",
        side_effect=InvalidOperation("quantize failed"),
    ):
        with pytest.raises(InvalidOperation):
            calculate_commission(Decimal("100.00"))
