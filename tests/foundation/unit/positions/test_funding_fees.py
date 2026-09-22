"""LB-4 — funding_fees 단위테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-4.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency, FXRate, Money
from src.foundation.positions.domain import funding_fees, fx as fx_module

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

    with pytest.raises(fx_module.FxRateMissingError):
        funding_fees.to_base(fee, Currency.KRW, None)


def test_to_base_converts_via_fx_rate() -> None:
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)

    result = funding_fees.to_base(fee, Currency.KRW, _rate())

    assert result == Decimal("1350")


def test_to_base_stale_rate_raises() -> None:
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    stale_rate = _rate(age=timedelta(hours=1))

    with pytest.raises(fx_module.FxRateStaleError):
        funding_fees.to_base(fee, Currency.KRW, stale_rate, now=_NOW, max_age=timedelta(minutes=5))


def test_funding_amount_then_to_base_round_trip_sum_preserved() -> None:
    """펀딩액을 base로 환산해도 별도 반올림을 하지 않으므로, 같은 환율로
    역산하면 정확히 원래 값으로 돌아온다(잔차 없음)."""
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)
    funding = funding_fees.funding_amount(Decimal("10"), mark, Decimal("0.001"))
    rate = _rate()

    base_amount = funding_fees.to_base(funding, Currency.KRW, rate)

    assert base_amount == funding.amount * rate.rate


# ── DEEPEN: negative / 실패주입 / 성능 단언 ──────────────────────────


def test_funding_amount_zero_notional_yields_zero_regardless_of_rate() -> None:
    """불변식: mark 금액이 0이면 펀딩액도 0이어야 한다(rate가 아무리 커도)."""
    mark = Money(amount=Decimal("0"), currency=Currency.USDT)

    result = funding_fees.funding_amount(Decimal("10"), mark, Decimal("999"))

    assert result.amount == Decimal("0")
    assert result.currency == Currency.USDT


def test_to_base_rejects_unrelated_rate_pair() -> None:
    """불변식: 요청한 통화쌍과 무관한 환율은 삼각환산 금지 원칙에 따라
    미존재로 취급해야 한다(LB-4 §3.2). USDT→KRW 요청에 KRW→KRW(자기
    자신 대상) 환율은 정방향/역방향 둘 다 매칭되지 않아야 한다."""
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    unrelated_rate = FXRate(
        base=Currency.KRW, quote=Currency.KRW, rate=Decimal("1"), timestamp=_NOW, source="test"
    )

    with pytest.raises(fx_module.FxRateMissingError):
        funding_fees.to_base(fee, Currency.KRW, unrelated_rate)


def test_funding_amount_negative_rate_flips_sign() -> None:
    """음의 펀딩률: 롱 포지션이 펀딩을 수령한다(양액)."""
    mark = Money(amount=Decimal("100"), currency=Currency.USDT)

    result = funding_fees.funding_amount(Decimal("10"), mark, Decimal("-0.001"))

    assert result.amount == Decimal("-1")


def test_to_base_propagates_fx_convert_exception_via_monkeypatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `fx.convert`가 예외를 던지면 `to_base`가 삼키지 않고
    그대로 전파해야 한다(fail-closed). 잘못된 환산값이 조용히 0으로
    대체되는 것을 방지한다."""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise fx_module.FxRateMissingError(Currency.USDT, Currency.KRW)

    monkeypatch.setattr(fx_module, "convert", _boom)
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)

    with pytest.raises(fx_module.FxRateMissingError):
        funding_fees.to_base(fee, Currency.KRW, _rate())


def test_to_base_batch_10000_calls_within_latency_budget() -> None:
    """수치 성능 단언: 10,000회 환산이 50ms 예산 안에 끝나야 한다
    (순수 Decimal 연산 — O(1) per call, 실측 ~0.001초)."""
    import time

    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    rate = _rate()
    n = 10_000
    budget = 0.050  # 50ms

    t0 = time.perf_counter()
    for _ in range(n):
        funding_fees.to_base(fee, Currency.KRW, rate)
    elapsed = time.perf_counter() - t0

    assert elapsed < budget, f"환산 {n}회 기준 {elapsed:.4f}s — 예산 {budget}s 초과"


def test_to_base_accepts_reverse_rate_and_inverts() -> None:
    """불변식: reverse rate(KRW→USDT)를 USDT→KRW 환산에 건네면, 역수로
    계산해야 한다(삼각환산 금지 원칙 하에서 유일한 경로). USDT 1 × (1/1350) = 0.000740740..."""
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    reverse_rate = FXRate(
        base=Currency.KRW,
        quote=Currency.USDT,
        rate=Decimal("1") / Decimal("1350"),
        timestamp=_NOW,
        source="test",
    )

    result = funding_fees.to_base(fee, Currency.KRW, reverse_rate)

    assert result == Decimal("1") / (Decimal("1") / Decimal("1350"))


def test_to_base_rejects_zero_rate() -> None:
    """불변식: 환율이 정확히 0이면(역수 계산 시 0으로 나누기 방지) reverse path도
    삼각환산으로 취급해 FxRateMissingError를 발생시켜야 한다."""
    fee = Money(amount=Decimal("1"), currency=Currency.USDT)
    zero_rate = FXRate(
        base=Currency.KRW,
        quote=Currency.USDT,
        rate=Decimal("0"),
        timestamp=_NOW,
        source="test",
    )

    with pytest.raises(fx_module.FxRateMissingError):
        funding_fees.to_base(fee, Currency.KRW, zero_rate)
