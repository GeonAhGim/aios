"""LB-20 — fx_forward 단위테스트.

Spec: docs/design/ADR-2026-09-06-G-second-audit-corrections.md §9 LB-20
DoD("헤지 미실현 FX 손익이 NAV 분해에서 자산 손익과 분리 표시, 미헤지
익스포저 리포트가 수기 계산과 소수 4자리까지 일치, 환율 출처가 known_at과
함께 기록돼 재현 가능").
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.domain import fx_forward as fxf

_KNOWN_AT = datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc)
_SETTLEMENT = date(2026, 12, 7)


def _quote(
    *,
    base: Currency = Currency.USDT,
    quote: Currency = Currency.KRW,
    rate: str = "1330",
    settlement_date: date = _SETTLEMENT,
    known_at: datetime = _KNOWN_AT,
    source: str = "test-desk",
) -> fxf.ForwardQuote:
    return fxf.ForwardQuote(
        base=base,
        quote=quote,
        rate=Decimal(rate),
        settlement_date=settlement_date,
        known_at=known_at,
        source=source,
    )


def _contract(
    *,
    contract_id: str = "hedge-1",
    base: Currency = Currency.USDT,
    quote: Currency = Currency.KRW,
    notional: str = "1000",
    contract_rate: str = "1350",
    settlement_date: date = _SETTLEMENT,
) -> fxf.FxHedgeContract:
    return fxf.FxHedgeContract(
        contract_id=contract_id,
        base=base,
        quote=quote,
        notional=Decimal(notional),
        contract_rate=Decimal(contract_rate),
        settlement_date=settlement_date,
        known_at=_KNOWN_AT,
        source="test-desk",
    )


def test_hedge_unrealized_pnl_matches_hand_calc_and_carries_rate_provenance() -> None:
    contract = _contract(notional="1000", contract_rate="1350")
    market = _quote(rate="1330", source="reuters-fwd", known_at=_KNOWN_AT)

    result = fxf.hedge_unrealized_pnl(contract, market)

    # 손매도(1350) - 현재 선물환(1330) = 20/USDT * 1000 = 20000 KRW 이익.
    assert result.unrealized.amount == Decimal("20000")
    assert result.unrealized.currency == Currency.KRW
    assert result.rate_source == "reuters-fwd"
    assert result.rate_known_at == _KNOWN_AT


def test_hedge_unrealized_pnl_long_forward_negative_notional_flips_sign() -> None:
    contract = _contract(notional="-1000", contract_rate="1350")
    market = _quote(rate="1330")

    result = fxf.hedge_unrealized_pnl(contract, market)

    assert result.unrealized.amount == Decimal("-20000")


def test_hedge_unrealized_pnl_rejects_mismatched_settlement_date() -> None:
    contract = _contract(settlement_date=_SETTLEMENT)
    other_tenor = _quote(settlement_date=date(2027, 3, 7))

    with pytest.raises(fxf.ForwardRateMismatchError):
        fxf.hedge_unrealized_pnl(contract, other_tenor)


def test_hedge_unrealized_pnl_rejects_mismatched_currency_pair() -> None:
    contract = _contract(base=Currency.USDT, quote=Currency.KRW)
    wrong_pair = _quote(base=Currency.KRW, quote=Currency.USDT)

    with pytest.raises(fxf.ForwardRateMismatchError):
        fxf.hedge_unrealized_pnl(contract, wrong_pair)


def test_decompose_fx_pnl_separates_asset_from_hedge() -> None:
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.KRW)
    hedge_pnl = fxf.HedgePnl(
        contract_id="hedge-1",
        unrealized=Money(amount=Decimal("20000"), currency=Currency.KRW),
        rate_source="reuters-fwd",
        rate_known_at=_KNOWN_AT,
    )

    breakdown = fxf.decompose_fx_pnl(asset_fx, [hedge_pnl])

    assert breakdown.asset_unrealized_fx == Decimal("-5000")
    assert breakdown.hedge_unrealized_fx == Decimal("20000")
    assert breakdown.net == Decimal("15000")
    assert breakdown.currency == Currency.KRW


def test_decompose_fx_pnl_rejects_mixed_quote_currencies() -> None:
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.KRW)
    hedge_pnl = fxf.HedgePnl(
        contract_id="hedge-1",
        unrealized=Money(amount=Decimal("20"), currency=Currency.USDT),
        rate_source="reuters-fwd",
        rate_known_at=_KNOWN_AT,
    )

    with pytest.raises(fxf.HedgeQuoteCurrencyMismatchError):
        fxf.decompose_fx_pnl(asset_fx, [hedge_pnl])


def test_unhedged_exposure_matches_hand_calc_to_four_decimals() -> None:
    hedges = [
        _contract(contract_id="h1", notional="333.3333"),
        _contract(contract_id="h2", notional="111.1111"),
    ]

    result = fxf.unhedged_exposure(
        Decimal("1000.0000"), hedges, base=Currency.USDT, quote=Currency.KRW, quantize_to=4
    )

    # 수기 계산: 1000.0000 - (333.3333 + 111.1111) = 555.5556
    assert result == Decimal("555.5556")


def test_unhedged_exposure_ignores_hedges_for_other_currency_pairs() -> None:
    hedges = [
        _contract(contract_id="h1", notional="200", base=Currency.USDT, quote=Currency.KRW),
        _contract(contract_id="h2", notional="9999", base=Currency.KRW, quote=Currency.USDT),
    ]

    result = fxf.unhedged_exposure(
        Decimal("1000"), hedges, base=Currency.USDT, quote=Currency.KRW
    )

    assert result == Decimal("800")


def test_unhedged_exposure_without_quantize_keeps_full_precision() -> None:
    hedges = [_contract(notional="333.33333333")]

    result = fxf.unhedged_exposure(
        Decimal("1000"), hedges, base=Currency.USDT, quote=Currency.KRW
    )

    assert result == Decimal("666.66666667")
