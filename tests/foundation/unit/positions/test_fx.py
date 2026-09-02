"""LB-4 — fx 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-4
("환율 없음 → 예외").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.contracts.v1 import PositionErrorCode
from src.foundation.positions.domain import fx

_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)


def _rate(
    *,
    base: Currency = Currency.USDT,
    quote: Currency = Currency.KRW,
    rate: str = "1350",
    age: timedelta = timedelta(),
) -> FXRate:
    return FXRate(base=base, quote=quote, rate=Decimal(rate), timestamp=_NOW - age, source="test")


def test_convert_same_currency_is_identity_and_needs_no_rate() -> None:
    m = Money(amount=Decimal("100"), currency=Currency.KRW)

    result = fx.convert(m, Currency.KRW, None)

    assert result.amount == Decimal("100")
    assert result.currency == Currency.KRW
    assert result.rate is None


def test_convert_missing_rate_raises_without_silent_fallback() -> None:
    m = Money(amount=Decimal("100"), currency=Currency.USDT)

    with pytest.raises(fx.FxRateMissingError) as exc_info:
        fx.convert(m, Currency.KRW, None)

    assert exc_info.value.code == PositionErrorCode.FX_RATE_MISSING


def test_convert_direct_rate_multiplies() -> None:
    m = Money(amount=Decimal("10"), currency=Currency.USDT)
    rate = _rate(base=Currency.USDT, quote=Currency.KRW, rate="1350")

    result = fx.convert(m, Currency.KRW, rate)

    assert result.amount == Decimal("13500")
    assert result.currency == Currency.KRW
    assert result.rate is rate


def test_convert_inverse_rate_divides() -> None:
    m = Money(amount=Decimal("13500"), currency=Currency.KRW)
    rate = _rate(base=Currency.USDT, quote=Currency.KRW, rate="1350")

    result = fx.convert(m, Currency.USDT, rate)

    assert result.amount == Decimal("10")
    assert result.currency == Currency.USDT
    assert result.rate is rate


def test_convert_rejects_triangulated_rate_as_missing() -> None:
    """base/quote 둘 다 요청한 통화쌍과 무관한 환율은 삼각환산 금지 원칙에
    따라 미존재로 취급해야 한다."""
    m = Money(amount=Decimal("10"), currency=Currency.USDT)
    unrelated_rate = FXRate(
        base=Currency.KRW, quote=Currency.KRW, rate=Decimal("1"), timestamp=_NOW, source="test"
    )

    with pytest.raises(fx.FxRateMissingError):
        fx.convert(m, Currency.KRW, unrelated_rate)


def test_convert_stale_rate_raises_and_is_not_silently_used() -> None:
    m = Money(amount=Decimal("10"), currency=Currency.USDT)
    stale_rate = _rate(age=timedelta(hours=1))

    with pytest.raises(fx.FxRateStaleError) as exc_info:
        fx.convert(m, Currency.KRW, stale_rate, now=_NOW, max_age=timedelta(minutes=5))

    assert exc_info.value.code == PositionErrorCode.FX_RATE_MISSING
    assert isinstance(exc_info.value, fx.FxRateMissingError)


def test_convert_fresh_rate_within_max_age_succeeds() -> None:
    m = Money(amount=Decimal("10"), currency=Currency.USDT)
    fresh_rate = _rate(age=timedelta(minutes=1))

    result = fx.convert(m, Currency.KRW, fresh_rate, now=_NOW, max_age=timedelta(minutes=5))

    assert result.amount == Decimal("13500")


def test_convert_without_now_skips_staleness_check() -> None:
    m = Money(amount=Decimal("10"), currency=Currency.USDT)
    ancient_rate = _rate(age=timedelta(days=365))

    result = fx.convert(m, Currency.KRW, ancient_rate)

    assert result.amount == Decimal("13500")


def test_convert_zero_rate_on_inverse_direction_is_missing_not_divide_by_zero() -> None:
    m = Money(amount=Decimal("10"), currency=Currency.KRW)
    zero_rate = _rate(base=Currency.USDT, quote=Currency.KRW, rate="0")

    with pytest.raises(fx.FxRateMissingError):
        fx.convert(m, Currency.USDT, zero_rate)
