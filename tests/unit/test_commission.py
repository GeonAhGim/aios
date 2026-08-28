"""13.7 단위테스트 — 순수 계산 로직."""
from decimal import Decimal

from src.services.commission import calculate_commission


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
