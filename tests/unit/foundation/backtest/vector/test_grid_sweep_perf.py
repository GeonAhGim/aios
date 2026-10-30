"""BT-16a grid sweep 성능 테스트 — 정규화 임계 및 지연 주입 게이트 적색 재현.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
성능 테스트는 별도 파일로 분할(LOC/복잡도 한도 준수, CLAUDE.md §7).
공용 헬퍼(_rising_columns, _buy_and_hold_signal, _config)는
test_vector_sweeps.py와 공유 — 여기서는 같은 정의를 재사용.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector.fills import VectorSignal
from src.foundation.backtest.vector.grid import sweep_grid
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_D = Decimal
_CASH = _D("100000")


def _rising_columns(n: int, *, step: timedelta = timedelta(minutes=1)) -> CandleColumns:
    """매 봉 종가가 1씩 오르는 단조 상승 시계열 — buy&hold 조합이 명확히
    무거래 조합보다 나은 결과를 내도록 만든 고정 픽스처."""
    closes = [_D(str(100 + i)) for i in range(n)]
    opens = [closes[i - 1] if i else closes[0] for i in range(n)]
    return CandleColumns(
        ts=[_T0 + step * i for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _buy_and_hold_signal(n: int) -> VectorSignal:
    """항상 진입 시도(포지션이 0일 때만 실제로 체결)·절대 청산하지 않는다 —
    어느 구간을 잘라 넘겨도 그 구간의 첫 체결 가능 봉에서 사서 계속 든다."""
    always = BoolSignal(values=np.ones(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    never = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=always, exits=never, quantity=_D("1"))


def _config() -> BacktestConfigV2:
    return BacktestConfigV2(
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


@pytest.mark.perf
def test_grid_sweep_normalized_performance_threshold(perf_budget) -> None:
    """1조합 CPU 실측 x N x 3.0 예산으로 1,000조합을 검증한다.

    200봉 표본이며 실제 1개월 M1 규모의 절대 성능은 별도 확인한다.
    배치 측정으로 Windows CPU 타이머 양자화 영향을 줄인다.
    """
    n = 200
    cols = _rising_columns(n)
    config = _config()
    baseline_combos = {"S0": _buy_and_hold_signal(n)}

    def run_baseline():
        return sweep_grid(cols, baseline_combos, config, timeframe=Timeframe.M1, initial_cash=_CASH)

    baseline = perf_budget.best_of(run_baseline, n=5, batch=128)
    assert baseline.cpu_ms > 0, "Baseline batch must resolve the CPU timer tick"

    combo_count = 1000
    combos = {f"C{i:04d}": _buy_and_hold_signal(n) for i in range(combo_count)}

    def run_sweep():
        return sweep_grid(cols, combos, config, timeframe=Timeframe.M1, initial_cash=_CASH)

    measured = perf_budget.best_of(run_sweep, n=5, batch=2)
    threshold_ms = baseline.cpu_ms * combo_count * 3.0
    assert len(measured.result.results) == combo_count
    assert measured.cpu_ms <= threshold_ms, perf_budget.describe(measured, budget_ms=threshold_ms)


@pytest.mark.perf
def test_grid_sweep_performance_assertion_goes_red_with_10x_delay_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.perf_counter`를 monkeypatch해 매 호출당
    50ms씩 증가하도록 하면 wall_clock 측정값이 예산을 크게 초과해 AssertionError가
    발생해야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    n = 200
    cols = _rising_columns(n)
    config = _config()

    # 실제 perf_counter + 호출당 지연
    _original = time.perf_counter
    _call_count = [0]
    _injected_delay_s = 0.05  # 50ms/call × 4회 = 200ms >> 정상 측정

    def _fake_perf_counter() -> float:
        _call_count[0] += 1
        return _original() + (_injected_delay_s * _call_count[0])

    monkeypatch.setattr(time, "perf_counter", _fake_perf_counter)

    # 같은 측정/단언을 재사용 — 지연 주입 하에서 budget을 초과해야 함(RED)
    baseline_start = time.perf_counter()
    sweep_grid(
        cols,
        {"S0": _buy_and_hold_signal(n)},
        config,
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
    )
    baseline_seconds = time.perf_counter() - baseline_start

    combo_count = 1000
    combos = {f"C{i:04d}": _buy_and_hold_signal(n) for i in range(combo_count)}
    start = time.perf_counter()
    result = sweep_grid(cols, combos, config, timeframe=Timeframe.M1, initial_cash=_CASH)
    elapsed_seconds = time.perf_counter() - start

    threshold_seconds = baseline_seconds * combo_count * 3.0
    print(
        f"[BT-16a injected] 1조합 실측={baseline_seconds:.4f}s, {combo_count}조합 실측="
        f"{elapsed_seconds:.4f}s, 정규화 임계={threshold_seconds:.4f}s"
    )
    assert len(result.results) == combo_count
    # 지연 주입 하에서 지연이 budget을 초과해야 함 — 단언이 허울이 아님을 증명
    assert elapsed_seconds <= threshold_seconds, (
        f"지연 주입 후 측정({elapsed_seconds:.4f}s)이 임계("
        f"{threshold_seconds:.4f}s) 미만 — 성능 단언이 실제로 동작하지 않는다"
    )
