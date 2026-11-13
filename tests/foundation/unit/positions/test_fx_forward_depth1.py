"""LB-20 — fx_forward 깊이 하한 보강 D3 (depth-1 보조 테스트).

적대·리플레이·실패주입·게이트 적색 재현·perf 10배 지연 증빙을 추가해
D2 하한을 D3로 끌어올린다. 기존 test_fx_forward.py를 수정하지 않는다.

Spec: docs/design/ADR-2026-09-06-G-second-audit-corrections.md §9 LB-20
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.domain import fx_forward as fxf
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_KNOWN_AT = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
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


# ---- 음성(negative) 테스트 3종 -------------------------------------------


def test_depth1_inverse_pair_quote_spoof_is_rejected() -> None:
    """base/quote를 뒤집고 rate를 역수로 넣은 시세는 ForwardRateMismatchError."""
    contract = _contract(base=Currency.USDT, quote=Currency.KRW)
    # base/quote 역전: KRW/USDT, rate를 역수로 (1/1330)
    spoofed = _quote(
        base=Currency.KRW,
        quote=Currency.USDT,
        rate="0.00075188",  # 1/1330 근사
    )
    with pytest.raises(fxf.ForwardRateMismatchError) as exc_info:
        fxf.hedge_unrealized_pnl(contract, spoofed)
    err = exc_info.value
    assert isinstance(err.expected, tuple)
    assert err.got is spoofed


def test_depth1_settlement_off_by_one_day_is_rejected() -> None:
    """결제일이 하루 어긋난 시세는 ForwardRateMismatchError."""
    contract = _contract(settlement_date=_SETTLEMENT)
    off_by_one = _quote(settlement_date=date(2026, 12, 8))
    with pytest.raises(fxf.ForwardRateMismatchError) as exc_info:
        fxf.hedge_unrealized_pnl(contract, off_by_one)
    err = exc_info.value
    # expected에 contract의 settlement이 들어 있어야 함
    assert err.expected[2] == _SETTLEMENT
    assert err.got.settlement_date == date(2026, 12, 8)


def test_depth1_decompose_rejects_asset_currency_differing_from_hedge() -> None:
    """자산 USDT, 헤지 KRW를 넘기면 HedgeQuoteCurrencyMismatchError."""
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.USDT)
    hedge_pnl = fxf.HedgePnl(
        contract_id="hedge-1",
        unrealized=Money(amount=Decimal("20000"), currency=Currency.KRW),
        rate_source="test-desk",
        rate_known_at=_KNOWN_AT,
    )
    with pytest.raises(fxf.HedgeQuoteCurrencyMismatchError) as exc_info:
        fxf.decompose_fx_pnl(asset_fx, [hedge_pnl])
    err = exc_info.value
    assert err.currencies == {Currency.USDT, Currency.KRW}


# ---- 리플레이 테스트 1종 -------------------------------------------------


def test_depth1_replay_same_inputs_reproduces_identical_provenance() -> None:
    """같은 contract/quote로 두 번 호출한 HedgePnl이 금액·rate_source·rate_known_at까지 동일."""
    contract = _contract(notional="1000", contract_rate="1350")
    market = _quote(rate="1330", source="reuters-fwd", known_at=_KNOWN_AT)

    result_a = fxf.hedge_unrealized_pnl(contract, market)
    result_b = fxf.hedge_unrealized_pnl(contract, market)

    assert result_a.unrealized.amount == result_b.unrealized.amount
    assert result_a.unrealized.currency == result_b.unrealized.currency
    assert result_a.rate_source == result_b.rate_source
    assert result_a.rate_known_at == result_b.rate_known_at
    assert result_a.contract_id == result_b.contract_id


# ---- 실패 주입 테스트 1종 -----------------------------------------------


def test_depth1_mismatch_error_carries_source_and_known_at() -> None:
    """예외 메시지와 got 필드에 source와 known_at이 실려 있는지 확인.

    ForwardRateMismatchError는 (base, quote, settlement_date) 불일치 시
    발생하므로, currency pair를 뒤집고 source/known_at을 특정값으로
    설정해 예외에 실린 필드를 검증한다.
    """
    contract = _contract(base=Currency.USDT, quote=Currency.KRW)
    wrong = _quote(
        base=Currency.KRW,
        quote=Currency.USDT,
        source="spoof-src",
        known_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    with pytest.raises(fxf.ForwardRateMismatchError) as exc_info:
        fxf.hedge_unrealized_pnl(contract, wrong)
    err = exc_info.value
    # got 필드에 source와 known_at이 있어야 함
    assert err.got.source == "spoof-src"
    assert err.got.known_at == datetime(2025, 1, 1, tzinfo=UTC)
    # 메시지에 source가 포함되어 있어야 함
    assert "spoof-src" in str(err)


# ---- 게이트 적색 재현 2종 -----------------------------------------------


def test_depth1_one_tick_drift_trips_hand_calc_assertion() -> None:
    """market rate를 1329로 한 틱 어긋나게 넣고, 1350/1330 기준 기대값 20000을
    단언하는 블록이 pytest.raises(AssertionError)로 잡히는지 확인."""
    contract = _contract(notional="1000", contract_rate="1350")
    # 한 틱 어긋남: 1329 (정상은 1330)
    market = _quote(rate="1329")
    result = fxf.hedge_unrealized_pnl(contract, market)
    # 1350-1329=21 * 1000=21000. 1330 기준 20000과 다르다.
    with pytest.raises(AssertionError):
        assert result.unrealized.amount == Decimal("20000"), (
            "1 tick drift: expected 20000 (1350-1330), got "
            f"{result.unrealized.amount} (1350-1329=21000)"
        )


@pytest.mark.perf
def test_depth1_virtual_10x_injection_trips_cpu_budget(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """perf 게이트 적색 재현: 10배 가상 지연(0.010s)이 1ms 예산을 초과하는지 확인.

    시작 시 reset_virtual_totals() 호출 → try/finally로 정리.
    기존 1ms 예산을 그대로 사용. 새 예산 숫자를 만들지 않는다.
    """
    contract = _contract(notional="1000", contract_rate="1350")
    market = _quote(rate="1330", source="reuters-fwd", known_at=_KNOWN_AT)

    reset_virtual_totals()

    _original_hedge = fxf.hedge_unrealized_pnl
    try:
        monkeypatch.setattr(
            fxf, "hedge_unrealized_pnl", virtual_delayed_sync(_original_hedge, 0.010)
        )

        samples = perf_budget.best_of(
            lambda: fxf.hedge_unrealized_pnl(contract, market),
            n=5,
            warmup=1,
        )

        print(
            f"[LB-20 depth1 perf-red injected] "
            f"cpu={samples.cpu_ms:.3f}ms budget<1ms "
            f"(virtual delay=10ms/call, best-of-5)"
        )
        with pytest.raises(AssertionError):
            assert samples.cpu_ms < 1.0, (
                f"cpu={samples.cpu_ms:.3f}ms > 1ms budget (virtual delay=10ms/call, gate-red repro)"
            )
    finally:
        reset_virtual_totals()
        monkeypatch.setattr(fxf, "hedge_unrealized_pnl", _original_hedge)
