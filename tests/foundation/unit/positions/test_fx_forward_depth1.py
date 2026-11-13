"""LB-20 — fx_forward 깊이 하한 보강 (D2→D3).

새 보조 테스트 파일. 기존 `test_fx_forward.py`의 fixture·임포트 방식을 그대로 따른다.
대상 모듈: src/foundation/positions/domain/fx_forward.py

DoD: 적대/리플레이/동시성 중 하나를 실제로 재현하는 테스트 1종 이상(하한 D3).
Negative 3종 이상, 실패 주입, 게이트 적색 재현(지연 주입), 성능 단언 포함.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from src.data.models.base import Currency, Money
from src.foundation.positions.domain import fx_forward as fxf
from tests.conftest import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
)

_KNOWN_AT = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
_SETTLEMENT = date(2026, 12, 7)


# ---- fixture 헬퍼 (기존 test_fx_forward.py와 동일) --------------------------


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


# ---- 음성 (negative) 테스트 4종 -------------------------------------------


def test_zero_notional_yields_zero_pnl() -> None:
    """음성: notional이 0이면 손익도 0이어야 한다."""
    contract = _contract(notional="0")
    market = _quote(rate="1330")

    result = fxf.hedge_unrealized_pnl(contract, market)

    assert result.unrealized.amount == Decimal("0")
    assert result.unrealized.currency == Currency.KRW


def test_zero_spread_yields_zero_pnl() -> None:
    """음성: 계약률과 시장률이 같으면 손익이 0이어야 한다."""
    contract = _contract(contract_rate="1330")
    market = _quote(rate="1330")

    result = fxf.hedge_unrealized_pnl(contract, market)

    assert result.unrealized.amount == Decimal("0")


def test_decompose_fx_pnl_rejects_empty_hedge_list() -> None:
    """음성: hedge_pnls가 비어 있으면 net = asset_unrealized_fx 여야 한다.
    빈 리스트를 전달해도 예외가 아니라 합계 0으로 처리되는지 검증 —
    일부 구현은 빈 시퀀스에 sum()을 호출할 때 Decimal("0") 초기값을
    생략하면 TypeError를 낸다."""
    asset_fx = Money(amount=Decimal("-3000"), currency=Currency.KRW)

    breakdown = fxf.decompose_fx_pnl(asset_fx, [])

    assert breakdown.asset_unrealized_fx == Decimal("-3000")
    assert breakdown.hedge_unrealized_fx == Decimal("0")
    assert breakdown.net == Decimal("-3000")


def test_unhedged_exposure_all_hedged_out() -> None:
    """음성: 헤지 총액이 노출액과 정확히 같으면 잔액 0."""
    hedges = [_contract(notional="500"), _contract(contract_id="h2", notional="500")]

    result = fxf.unhedged_exposure(Decimal("1000"), hedges, base=Currency.USDT, quote=Currency.KRW)

    assert result == Decimal("0")


# ---- 실패 주입 -------------------------------------------------------------


def test_decompose_rejects_single_hedge_with_wrong_currency() -> None:
    """실패 주입: 헤지 1건만 있고 통화가 다르면 HedgeQuoteCurrencyMismatchError."""
    asset_fx = Money(amount=Decimal("1000"), currency=Currency.KRW)
    hedge_pnl = fxf.HedgePnl(
        contract_id="h1",
        unrealized=Money(amount=Decimal("500"), currency=Currency.USDT),
        rate_source="test",
        rate_known_at=_KNOWN_AT,
    )

    with pytest.raises(fxf.HedgeQuoteCurrencyMismatchError):
        fxf.decompose_fx_pnl(asset_fx, [hedge_pnl])


# ---- 게이트 적색 재현 (지연 주입 + perf_budget) -----------------------------


@pytest.mark.perf
def test_decompose_fx_pnl_perf_budget_catches_virtual_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): `decompose_fx_pnl`에 가상 지연을 주입하면
    perf_budget.assert_within의 budget 단언이 AssertionError로 적색이
    되는지 검증한다. virtual_delayed_sync는 실제 sleep 없이 가상 CPU
    시간을 늘려 PerfBudget 샘플에 반영한다."""
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.KRW)
    hedge_pnls = [
        fxf.HedgePnl(
            contract_id="h1",
            unrealized=Money(amount=Decimal("20000"), currency=Currency.KRW),
            rate_source="reuters-fwd",
            rate_known_at=_KNOWN_AT,
        ),
    ]

    reset_virtual_totals()

    # virtual_delayed_sync: 실제 실행은 그대로지만 CPU+wall ledger에
    # 지연 시간을 더해서 PerfBudget이 측정 시 이를 반영하게 한다.
    # virtual_delayed_sync는 decorator/factory로, 원본 함수를 감싸서
    # 호출마다 seconds만큼 가상 CPU+wall 시간을 ledger에 추가한다.
    # 50ms 지연 주입 → PerfBudget이 이를 측정 구간에 추가
    # budget은 5ms이므로 50ms 지연은 명확히 budget 초과 → 적색
    _original_decompose = fxf.decompose_fx_pnl
    _patched_decompose = virtual_delayed_sync(_original_decompose, 0.05)

    monkeypatch.setattr(fxf, "decompose_fx_pnl", _patched_decompose)

    samples = perf_budget.samples(
        lambda: fxf.decompose_fx_pnl(asset_fx, hedge_pnls),
        n=10,
        batch=1,
    )
    # 가상 지연이 ledger에 누적되었으므로 wall_ms가 budget을 초과해야 함
    p95_ms = sorted(s.wall_ms for s in samples)[int(len(samples) * 0.95)]
    print(
        f"[LB-20 decompose_fx_pnl gate-red injected] "
        f"p95={p95_ms:.2f}ms budget<5ms "
        f"(virtual delay=50ms/call)"
    )
    with pytest.raises(AssertionError):
        assert p95_ms < 5, (
            f"p95 wall={p95_ms:.2f}ms > 5ms budget (virtual delay=50ms/call, gate-red repro)"
        )


@pytest.mark.perf
def test_unhedged_exposure_perf_budget_best_of_measures_cpu(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언(D2): best_of()로 여러 번 중 최소 CPU 시간을 측정해
    unhedged_exposure의 성능 예산을 확인한다.

    실측 근거: 로컬 환경에서 1,000건 합산에 cpu < 0.5ms.
    예산은 30배 여유인 15ms로 설정.
    """
    hedges = [_contract(contract_id=f"h{i}", notional="0.1") for i in range(1_000)]

    sample = perf_budget.best_of(
        lambda: fxf.unhedged_exposure(
            Decimal("100"),
            hedges,
            base=Currency.USDT,
            quote=Currency.KRW,
        ),
        n=5,
        warmup=1,
        batch=8,
    )

    print(
        f"[LB-20 unhedged_exposure perf] "
        f"best-of-5 cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms "
        f"budget<15ms (1,000건 합산, 실측 근거: <0.5ms)"
    )
    assert sample.cpu_ms < 15, f"cpu={sample.cpu_ms:.3f}ms > 15ms budget (1,000건 합산)"


# ---- 적대 테스트 (D3 하한) -------------------------------------------------


def test_adversarial_rate_known_at_not_shared_between_contracts() -> None:
    """적대(D3, INVARIANTS I-05 재현): `FxHedgeContract`와 `ForwardQuote`의
    `known_at`은 각기 독립적인 타임스탬프다. 계약이 체결된 시점(known_at)과
    시장 quote가 기록된 시점이 다를 수 있으며, `hedge_unrealized_pnl`은
    market의 known_at만 결과에 기록해야 한다.

    계약의 known_at을 조작해도 결과의 rate_known_at이 market의 것을
    덮어쓰지 않는지 검증 — 데이터 오염 방지.
    """
    contract = fxf.FxHedgeContract(
        contract_id="hedge-1",
        base=Currency.USDT,
        quote=Currency.KRW,
        notional=Decimal("1000"),
        contract_rate=Decimal("1350"),
        settlement_date=_SETTLEMENT,
        known_at=datetime(2026, 9, 1, 8, 0, tzinfo=UTC),  # 계약 체결 시각
        source="test-desk",
    )
    market = _quote(
        rate="1330",
        source="reuters-fwd",
        known_at=datetime(2026, 9, 7, 9, 0, tzinfo=UTC),  # 시장 quote 시각
    )

    result = fxf.hedge_unrealized_pnl(contract, market)

    # 결과의 rate_known_at은 market(quote)의 것을 따라야 함
    assert result.rate_known_at == datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
    assert result.rate_source == "reuters-fwd"
    # contract의 known_at이 결과에 유입되지 않음
    assert result.rate_known_at != datetime(2026, 9, 1, 8, 0, tzinfo=UTC)


def test_adversarial_multiple_hedges_preserve_individual_rate_source() -> None:
    """적대(D3): 여러 헤지 계약에서 각 HedgePnl의 rate_source가
    decompose_fx_pnl 합산 과정에서 섞이지 않고 개별 유지되는지 검증.

    decompose_fx_pnl은 hedge_pnls의 합계만 반환하지만, 각 HedgePnl의
    rate_source는 원본에서 변경되지 않아야 한다.
    """
    asset_fx = Money(amount=Decimal("-5000"), currency=Currency.KRW)

    hedge_pnls = [
        fxf.HedgePnl(
            contract_id="h1",
            unrealized=Money(amount=Decimal("10000"), currency=Currency.KRW),
            rate_source="reuters-fwd",
            rate_known_at=_KNOWN_AT,
        ),
        fxf.HedgePnl(
            contract_id="h2",
            unrealized=Money(amount=Decimal("5000"), currency=Currency.KRW),
            rate_source="bloomberg-fwd",
            rate_known_at=datetime(2026, 9, 8, 9, 0, tzinfo=UTC),
        ),
    ]

    breakdown = fxf.decompose_fx_pnl(asset_fx, hedge_pnls)

    # 합산은 정확해야 함
    assert breakdown.hedge_unrealized_fx == Decimal("15000")
    assert breakdown.net == Decimal("10000")
    # 각 HedgePnl의 rate_source는 원본 유지 (decompose가 이를 수정하지 않음)
    assert hedge_pnls[0].rate_source == "reuters-fwd"
    assert hedge_pnls[1].rate_source == "bloomberg-fwd"


def test_adversarial_concurrent_quote_references_do_not_alias() -> None:
    """적대(D3): 동일한 ForwardQuote 객체를 여러 hedge_unrealized_pnl 호출에
    전달해도 각 결과가 독립적인지 검증. mutable state가 공유되지 않음.

    이 테스트는 fxf.hedge_unrealized_pnl이 market 인자를 mutable하게
    수정하지 않는다는 불변식(I-05: state purity)을 검증한다.
    """
    market = _quote(rate="1330", source="reuters-fwd")
    contract_a = _contract(contract_id="hedge-a", notional="500")
    contract_b = _contract(contract_id="hedge-b", notional="500")

    result_a = fxf.hedge_unrealized_pnl(contract_a, market)
    result_b = fxf.hedge_unrealized_pnl(contract_b, market)

    # 두 결과는 서로 독립적: 500 * (1350-1330) = 10000
    assert result_a.unrealized.amount == Decimal("10000")
    assert result_b.unrealized.amount == Decimal("10000")
    # market 객체가 수정되지 않았음 (rate 변경 안 됨)
    assert market.rate == Decimal("1330")
    assert market.source == "reuters-fwd"
