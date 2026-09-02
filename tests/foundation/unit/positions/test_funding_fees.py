"""LB-4 — funding_fees 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-4.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.domain import funding_fees, fx

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _rate(*, age: timedelta = timedelta()) -> FXRate:
    return FXRate(
        base=Currency.USDT,
        quote=Currency.KRW,
        rate=Decimal("1350"),
        timestamp=_NOW - age,
        source="test",
    )


def test_funding_amount_long_position_positive_rate() -> None:
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)

    result = funding_fees.funding_amount(Decimal("10"), mark, Decimal("0.001"))

    assert result.amount == Decimal("1")  # 10 * 100 * 0.001
    assert result.currency == Currency.USDT


def test_funding_amount_short_position_flips_sign() -> None:
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)

    result = funding_fees.funding_amount(Decimal("-10"), mark, Decimal("0.001"))

    assert result.amount == Decimal("-1")


def test_funding_amount_zero_rate_is_zero() -> None:
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)

    result = funding_fees.funding_amount(Decimal("10"), mark, Decimal("0"))

    assert result.amount == Decimal("0")


def test_to_base_none_amount_is_zero_and_needs_no_rate() -> None:
    result = funding_fees.to_base(None, Currency.KRW, None)

    assert result == Decimal("0")


def test_to_base_same_currency_needs_no_rate() -> None:
    fee = Money(amount=Decimal("1.5"), currency=Currency.KRW)

    result = funding_fees.to_base(fee, Currency.KRW, None)

    assert result == Decimal("1.5")


def test_to_base_missing_rate_raises_no_silent_fallback() -> None:
    fee = Money(amount=Decimal("1.5"), currency=Currency.USDT)

    with pytest.raises(fx.FxRateMissingError):
        funding_fees.to_base(fee, Currency.KRW, None)


def test_to_base_converts_via_fx_rate() -> None:
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)

    result = funding_fees.to_base(fee, Currency.KRW, _rate())

    assert result == Decimal("1350")


def test_to_base_stale_rate_raises() -> None:
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    stale_rate = _rate(age=timedelta(hours=1))

    with pytest.raises(fx.FxRateStaleError):
        funding_fees.to_base(fee, Currency.KRW, stale_rate, now=_NOW, max_age=timedelta(minutes=5))


def test_funding_amount_then_to_base_round_trip_sum_preserved() -> None:
    """펀딩액을 base로 환산해도 별도 반올림을 하지 않으므로, 같은 환율로
    역산하면 정확히 원래 값으로 돌아온다(잔차 없음)."""
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)
    funding = funding_fees.funding_amount(Decimal("10"), mark, Decimal("0.001"))
    rate = _rate()

    base_amount = funding_fees.to_base(funding, Currency.KRW, rate)

    assert base_amount == funding.amount * rate.rate
