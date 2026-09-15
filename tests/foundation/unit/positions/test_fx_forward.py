"""LB-20 — fx_forward 단위테스트.

Spec: docs/design/ADR-2026-09-06-G-second-audit-corrections.md §9 LB-20
DoD("헤지 미실현 FX 손익이 NAV 분해에서 자산 손익과 분리 표시, 미헤지
익스포저 리포트가 수기 계산과 소수 4자리까지 일치, 환율 출처가 known_at과
함께 기록돼 재현 가능").
"""

from __future__ import annotations

import decimal
import time
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

    result = fxf.unhedged_exposure(Decimal("1000"), hedges, base=Currency.USDT, quote=Currency.KRW)

    assert result == Decimal("800")


def test_unhedged_exposure_without_quantize_keeps_full_precision() -> None:
    hedges = [_contract(notional="333.33333333")]

    result = fxf.unhedged_exposure(Decimal("1000"), hedges, base=Currency.USDT, quote=Currency.KRW)

    assert result == Decimal("666.66666667")


def test_hedge_unrealized_pnl_signaling_nan_rate_fails_loud_not_silent() -> None:
    """실패 주입: 상류 피드가 파싱 버그로 시그널링 NaN을 보내는 상황을
    흉내낸다. Decimal 기본 컨텍스트는 InvalidOperation을 트랩하므로 연산
    단계에서 즉시 예외가 나야 한다 — Money로 감싸져 NAV 분해에 "유효해
    보이는" 숫자로 조용히 흘러들어가면 안 된다(fail-closed)."""
    contract = _contract(notional="1000", contract_rate="1350")
    corrupted = _quote(rate="sNaN")

    with pytest.raises(decimal.InvalidOperation):
        fxf.hedge_unrealized_pnl(contract, corrupted)


def test_unhedged_exposure_perf_and_precision_at_volume() -> None:
    """수치 성능/지연 단언: 기존 수치검증(소규모 hand-calc 4자리 일치)은
    정확성만 봤다. 여기서는 10,000건 규모(매칭 통화쌍 5,000 + 무관 통화쌍
    5,000)에서 (1) 선형 시간 내 완료해 루프/재계산 성능 회귀를 잡고 (2)
    대량 합산에서도 Decimal 정밀도가 깨지지 않고 여전히 소수 4자리까지
    수기 계산과 일치함을 함께 증명한다."""
    matching = [_contract(contract_id=f"m{i}", notional="0.1") for i in range(5_000)]
    other_pair = [
        _contract(
            contract_id=f"o{i}",
            notional="9999.9999",
            base=Currency.KRW,
            quote=Currency.USDT,
        )
        for i in range(5_000)
    ]
    hedges = matching + other_pair

    start = time.perf_counter()
    result = fxf.unhedged_exposure(
        Decimal("5000.0000"), hedges, base=Currency.USDT, quote=Currency.KRW, quantize_to=4
    )
    elapsed = time.perf_counter() - start

    # 수기 계산: 5000.0000 - (5000 * 0.1) = 4500.0000 (다른 통화쌍 5,000건은 무시)
    assert result == Decimal("4500.0000")
    assert elapsed < 2.0


def test_decompose_fx_pnl_detects_mismatch_hidden_among_many_matching_hedges() -> None:
    """게이트 적색 재현: HedgeQuoteCurrencyMismatchError는 전체 hedge_pnls의
    통화 집합을 모아 판정한다 — 앞쪽 몇 건만 비교하는 얕은(pairwise-adjacent)
    구현이었다면 맨 끝에 섞인 통화 불일치 1건을 놓쳤을 것이다. KRW 표시
    헤지 49건 사이에 USDT 표시 헤지 1건을 맨 끝에 섞어도 여전히 적색(예외)
    이 되는지 재현한다."""
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.KRW)
    matching_hedges = [
        fxf.HedgePnl(
            contract_id=f"h{i}",
            unrealized=Money(amount=Decimal("100"), currency=Currency.KRW),
            rate_source="test-desk",
            rate_known_at=_KNOWN_AT,
        )
        for i in range(49)
    ]
    mismatched = fxf.HedgePnl(
        contract_id="h-mismatch",
        unrealized=Money(amount=Decimal("20"), currency=Currency.USDT),
        rate_source="test-desk",
        rate_known_at=_KNOWN_AT,
    )

    with pytest.raises(fxf.HedgeQuoteCurrencyMismatchError) as exc_info:
        fxf.decompose_fx_pnl(asset_fx, [*matching_hedges, mismatched])

    assert exc_info.value.currencies == {Currency.KRW, Currency.USDT}
