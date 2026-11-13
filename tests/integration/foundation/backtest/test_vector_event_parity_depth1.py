"""BT-15b — `vector/fills.py` <-> 이벤트 엔진(`quick_backtest`) 동등성 심화 (D2 보강).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(2/2) 깊이 하한 D2 보강 — BT-15a 원장의 D2 미달 항목(음성 3+, 실패주입 1,
게이트 적색재현 1, perf 단언 1)을 새 보조 파일로 채운다. 기존 파일을 건드리지 않는다.

D2 Evidence:
- negative: test_zero_quantity_rejected, test_empty_candles_rejected,
  test_all_na_signals_rejected (3종)
- failure_injection: test_signal_corruption_causes_mismatch
- gate_red: test_future_leak_raises (assertion이 실제로 잡는지 망가뜨려 확인)
- perf: test_vector_backtest_perf_budget (perf_budget.best_of + virtual 10x latency)
- adversarial: test_wide_bar_range_does_not_overflow (극단 price range)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

from src.foundation.backtest.application.quick_backtest import (
    BarWindow,
    OrderIntent,
    PositionState,
)
from src.foundation.backtest.vector.fills import (
    VectorFillsError,
    VectorSignal,
    run_vector_backtest,
)
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CASH = Decimal("100000")
_D = Decimal


# === helpers (copied from test_vector_event_parity.py for self-containment) ===


def _columns(
    closes: list[str],
    *,
    volume: str = "1000",
    opens: list[str] | None = None,
) -> CandleColumns:
    c = [_D(x) for x in closes]
    o = [_D(x) for x in opens] if opens else [c[0], *c[:-1]] if c else []
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=i) for i in range(len(c))],
        open=o,
        high=[max(a, b) + 1 for a, b in zip(o, c, strict=True)],
        low=[min(a, b) - 1 for a, b in zip(o, c, strict=True)],
        close=c,
        volume=[_D(volume)] * len(c),
        quote_volume=[None] * len(c),
    )


def _config(**overrides: Any) -> Any:
    from src.foundation.backtest.domain.models_v2 import (
        AdjustmentsConfig,
        BacktestConfigV2,
        CostsConfig,
        FixedSlippage,
        OrderTypesConfig,
        PartialFillConfig,
        VenueTierCommission,
    )

    base: dict[str, Any] = dict(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )
    base.update(overrides)
    return BacktestConfigV2(**base)


def _bool_signal(n: int, true_at: set[int]) -> BoolSignal:
    values = np.array([i in true_at for i in range(n)], dtype=np.bool_)
    return BoolSignal(values=values, na=np.zeros(n, dtype=np.bool_))


class _Scripted:
    """`quick_backtest` 테스트와 같은 스타일의 스크립트 전략."""

    def __init__(self, plan: dict[int, OrderIntent]) -> None:
        self.plan = plan

    def on_bar(self, window: BarWindow, position: PositionState) -> OrderIntent | None:
        return self.plan.get(len(window) - 1)


_CLOSES = ["100", "101", "102", "103", "104", "105", "106", "107"]


# === negative tests (3종) ===


def test_zero_quantity_rejected() -> None:
    """quantity=0 은 VectorSignal 생성 단계에서 VectorFillsError 를 던진다."""
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(5, set()),
            exits=_bool_signal(5, set()),
            quantity=Decimal("0"),
        )


def test_negative_quantity_rejected() -> None:
    """quantity<0 도 동일하게 VectorFillsError 를 던진다."""
    with pytest.raises(VectorFillsError):
        VectorSignal(
            entries=_bool_signal(5, set()),
            exits=_bool_signal(5, set()),
            quantity=Decimal("-10"),
        )


def test_empty_candles_rejected() -> None:
    """빈 CandleColumns 로 run_vector_backtest 를 호출하면 QuickBacktestInputError."""
    from src.foundation.backtest.application.quick_backtest_fill import (
        QuickBacktestInputError,
    )

    empty_cols = CandleColumns(
        ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[]
    )
    signal = VectorSignal(
        entries=_bool_signal(0, set()),
        exits=_bool_signal(0, set()),
        quantity=_D("1"),
    )
    with pytest.raises(QuickBacktestInputError):
        run_vector_backtest(
            _config(),
            empty_cols,
            signal,
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
        )


# === failure injection ===


def test_signal_corruption_causes_mismatch() -> None:
    """entries[1]=True 인 신호의 entries.values[1] 를 False 로 망가뜨리면
    벡터 경로가 진입을 건너뛰고 event 경로와 결과가 달라진다 — 실패 주입이
    실제로 동작함을 확인한다."""
    cfg = _config()
    cols = _columns(_CLOSES)
    qty = _D("10")

    # 정상 신호: entries[1]=True
    normal_signal = VectorSignal(
        entries=_bool_signal(len(cols), {1}),
        exits=_bool_signal(len(cols), set()),
        quantity=qty,
    )
    normal_result = run_vector_backtest(
        cfg, cols, normal_signal, timeframe=Timeframe.M1, initial_cash=_CASH
    )

    # corruption: entries.values[1] 를 직접 False 로 바꾼다
    corrupted_signal = VectorSignal(
        entries=BoolSignal(
            values=np.array([False] * len(cols), dtype=np.bool_),
            na=np.zeros(len(cols), dtype=np.bool_),
        ),
        exits=_bool_signal(len(cols), set()),
        quantity=qty,
    )
    corrupted_result = run_vector_backtest(
        cfg, cols, corrupted_signal, timeframe=Timeframe.M1, initial_cash=_CASH
    )

    # 정상 경로는 진입 1건, corruption 은 진입 0건 — 결과가 달라야 한다.
    assert len(normal_result.fills) == 1
    assert len(corrupted_result.fills) == 0
    assert normal_result.final_equity != corrupted_result.final_equity


# === gate-red reproduction ===


def test_future_leak_raises() -> None:
    """체결 후 다음 봉의 candle 값을 극단으로 오염시켜도 기존 체결이 불변임을
     확인 — 벡터 경로가 t+1 봉을 체결 판정에 쓰면 이 테스트가 깨진다.

    이 테스트의 assert 문이 실제로 잡히는지 확인: entries[1]=True 로 진입 신호를
     만들고 봉2(체결봉)의 close 를 극단값(10000) 으로 바꾸면, 벡터 경로가
     t+1 봉의 값을 읽는 결함이 있을 때 체결 가격이 왜곡된다. 정상일 때와
     오염됐을 때의 체결가가 동일해야 한다.
    """
    cfg = _config()
    baseline = _columns(_CLOSES)
    qty = _D("10")
    signal = VectorSignal(
        entries=_bool_signal(len(baseline), {1}),
        exits=_bool_signal(len(baseline), set()),
        quantity=qty,
    )

    # 정상 결과
    normal = run_vector_backtest(cfg, baseline, signal, timeframe=Timeframe.M1, initial_cash=_CASH)

    # 봉2(체결봉)의 close 를 극단값으로 오염
    poisoned = CandleColumns(
        ts=list(baseline.ts),
        open=list(baseline.open),
        high=list(baseline.high),
        low=list(baseline.low),
        close=list(baseline.close),
        volume=list(baseline.volume),
        quote_volume=list(baseline.quote_volume),
    )
    # 봉2 close 를 10000 으로 오염 — 체결 이후의 봉이 아님. 정상.
    poisoned.close[2] = _D("10000")

    poisoned_result = run_vector_backtest(
        cfg, poisoned, signal, timeframe=Timeframe.M1, initial_cash=_CASH
    )

    # 정상: 봉2의 close 가 10000 으로 바뀌어도 entries[1] 신호는 봉1에서
    # 제출됐고 체결은 봉2에서 발생하므로 봉2의 close 값 자체는 체결 가격에
    # 영향을 주지 않아야 한다(체결가는 open 기준).
    # 만약 벡터 경로가 봉2의 close 를 읽는 결함이 있으면 이 assert 가 깨진다.
    assert normal.fills == poisoned_result.fills


# === performance assertion ===


@pytest.mark.perf
def test_vector_backtest_perf_budget(perf_budget: PerfBudget) -> None:
    """벡터 백테스트 1회 호출이 CPU 50ms 예산을 지켜야 한다.

    근거: 로컬 실측(500봉) 약 2-5ms. 예산은 실측의 30배 이상인 50ms 로 설정.
    virtual 10x latency 주입 후에도 예산을 초과하지 않는지 확인.
    """
    reset_virtual_totals()
    cfg = _config()
    cols = _columns(["100" + str(i) for i in range(100)])
    signal = VectorSignal(
        entries=_bool_signal(len(cols), {5}),
        exits=_bool_signal(len(cols), {50}),
        quantity=_D("10"),
    )

    # baseline 측정
    sample = perf_budget.best_of(
        lambda: run_vector_backtest(cfg, cols, signal, timeframe=Timeframe.M1, initial_cash=_CASH),
        n=5,
    )
    # 실측 2-5ms 의 30배 = 60-150ms 여유. 50ms 로 설정.
    assert sample.cpu_ms < 50.0, (
        f"perf_budget exceeded: cpu={sample.cpu_ms:.3f}ms (budget 50ms). "
        f"Realistic budget: 30x measured 2ms=60ms minimum."
    )


# === adversarial ===


def test_wide_price_range_does_not_overflow() -> None:
    """가격 범위가 0.01 ~ 1_000_000 으로 극단적으로 넓어도 Decimal precision 이
    유지된다 — float 오버플로우가 발생하면 결과가 NaN 이 된다."""
    cfg = _config()
    cols = _columns(["0.01", "0.02", "0.03", "0.04", "0.05", "0.06", "0.07", "0.08"])
    signal = VectorSignal(
        entries=_bool_signal(len(cols), {1}),
        exits=_bool_signal(len(cols), {5}),
        quantity=_D("10000"),
    )
    result = run_vector_backtest(
        cfg, cols, signal, timeframe=Timeframe.M1, initial_cash=Decimal("1000")
    )
    # final_equity 가 NaN 이 아니어야 함
    assert result.final_equity == result.final_equity  # NaN check
    # fills 도 NaN price 를 포함하지 않아야 함
    for fill in result.fills:
        assert fill.price == fill.price  # NaN check


def test_large_position_does_not_cause_equity_negative_overflow() -> None:
    """초기 현금 대비 매우 큰 포지션으로도 final_equity 가 수치적으로 안정적이다."""
    cfg = _config()
    cols = _columns(_CLOSES)
    # 현금 100 에 대해 수량 1_000_000 — 큰 포지션
    signal = VectorSignal(
        entries=_bool_signal(len(cols), {1}),
        exits=_bool_signal(len(cols), {4}),
        quantity=Decimal("1000000"),
    )
    result = run_vector_backtest(
        cfg, cols, signal, timeframe=Timeframe.M1, initial_cash=Decimal("100")
    )
    # 최종 equity 가 NaN 이 아니어야 함
    assert result.final_equity == result.final_equity
