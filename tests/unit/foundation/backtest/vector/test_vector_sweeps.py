"""BT-16a — `backtest/vector/{grid,walk_forward,monte_carlo}.py` 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-16(실행부만, task-2371 decision — AI-10 실험 원장 기록은 BT-16b).

- grid: (a) 조합 키 집합이 입력과 정확히 같음. (b) 빈 조합 허용(BT-17
  `sweep_universe` 선례와 동일). (c) 조합 하나가 실패하면 부분 결과 없이
  예외가 전파됨(negative). (d) 성능 — 1종목 실측 x N x 1.5 정규화 임계
  (BT-17과 동일 선례, 절대 초 단언 금지). 표본 규모는 §9.9 DoD의 "1,000
  조합·1개월 M1"을 문자 그대로 재현하지 않는다 — 그 규모로는 CI 매 실행마다
  분 단위가 걸려 스위트 예산을 넘긴다(미검증: 실제 그 규모의 절대 성능은
  별도로 확인해야 한다).
- walk_forward: (a) 창 규격 음수/0 거부(negative). (b) 빈 조합 거부
  (negative). (c) 데이터가 창 하나보다 짧으면 거부(negative). (d) 훈련
  구간 성과가 더 좋은 조합이 검증 구간에서 선택돼 채점됨(정확성).
- monte_carlo: (a) iterations<=0·백분위 범위 밖·equity_curve 길이 부족·
  0 자본 구간 거부(negative, 4종). (b) 같은 시드는 같은 결과(결정론).
  (c) 중앙값이 최종 자본 표본 범위 안에 있음(정확성).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector.fills import VectorFillsError, VectorSignal
from src.foundation.backtest.vector.grid import GridSweepResult, sweep_grid
from src.foundation.backtest.vector.monte_carlo import (
    MonteCarloError,
    run_monte_carlo,
)
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.backtest.vector.walk_forward import (
    WalkForwardError,
    WalkForwardWindowSpec,
    run_walk_forward,
)
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
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


def _no_trade_signal(n: int) -> VectorSignal:
    empty = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=empty, exits=empty, quantity=_D("1"))


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
        latency_ms=0, partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None, costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False), calendar="24x7",
    )


# ================= grid.py =================


def test_grid_sweep_result_keys_match_input_combos() -> None:
    n = 30
    cols = _rising_columns(n)
    combos = {"buy_hold": _buy_and_hold_signal(n), "no_trade": _no_trade_signal(n)}
    result = sweep_grid(cols, combos, _config(), timeframe=Timeframe.M1, initial_cash=_CASH)

    assert set(result.results.keys()) == set(combos.keys())
    assert result.results["no_trade"].final_equity == _CASH
    assert result.results["buy_hold"].final_equity > _CASH


def test_grid_sweep_empty_combos_returns_empty_result() -> None:
    result = sweep_grid(
        _rising_columns(10), {}, _config(), timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    assert isinstance(result, GridSweepResult)
    assert result.results == {}


def test_grid_sweep_bad_combo_raises_without_returning_partial_results() -> None:
    """조합 하나의 signal 길이가 캔들 수와 다르면(계약 위반) 그 앞에 있던
    "정상" 조합이 있어도 `GridSweepResult`가 아예 만들어지지 않는다 —
    함수가 값을 반환하기 전에 예외가 전파되므로 호출자는 부분 결과를 절대
    받을 수 없다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"ok": _no_trade_signal(n), "bad": _no_trade_signal(n - 1)}
    with pytest.raises(VectorFillsError):
        sweep_grid(cols, combos, _config(), timeframe=Timeframe.M1, initial_cash=_CASH)


def test_grid_sweep_normalized_performance_threshold() -> None:
    """절대 초 상한이 아니라 1조합 실측 x N x 1.5로 단언한다(BT-17과 동일
    선례, 공유 CI CPU 편차를 흡수하기 위함). N=1,000은 §9.9 DoD 문자
    그대로지만 봉 수는 200으로 줄였다 — 실제 1개월 M1(약 43,200봉) 규모는
    스위트 예산 밖이라 별도 확인이 필요하다(미검증)."""
    n = 200
    cols = _rising_columns(n)
    config = _config()

    baseline_start = time.perf_counter()
    sweep_grid(
        cols, {"S0": _buy_and_hold_signal(n)}, config,
        timeframe=Timeframe.M1, initial_cash=_CASH,
    )
    baseline_seconds = time.perf_counter() - baseline_start

    combo_count = 1000
    combos = {f"C{i:04d}": _buy_and_hold_signal(n) for i in range(combo_count)}
    start = time.perf_counter()
    result = sweep_grid(cols, combos, config, timeframe=Timeframe.M1, initial_cash=_CASH)
    elapsed_seconds = time.perf_counter() - start

    threshold_seconds = baseline_seconds * combo_count * 1.5
    print(
        f"[BT-16a] 1조합 실측={baseline_seconds:.4f}s, {combo_count}조합 실측="
        f"{elapsed_seconds:.4f}s, 정규화 임계={threshold_seconds:.4f}s (§9.9 목표: ≤60s)"
    )
    assert len(result.results) == combo_count
    assert elapsed_seconds <= threshold_seconds


# ================= walk_forward.py =================


def test_window_spec_rejects_non_positive_bars() -> None:
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=0, test_bars=5)
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=5, test_bars=0)
    with pytest.raises(WalkForwardError):
        WalkForwardWindowSpec(train_bars=5, test_bars=5, step_bars=0)


def test_walk_forward_rejects_empty_combos() -> None:
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _rising_columns(40), {}, _config(), WalkForwardWindowSpec(train_bars=10, test_bars=5),
            timeframe=Timeframe.M1, initial_cash=_CASH,
        )


def test_walk_forward_rejects_data_shorter_than_one_window() -> None:
    n = 10
    combos = {"buy_hold": _buy_and_hold_signal(n)}
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _rising_columns(n), combos, _config(),
            WalkForwardWindowSpec(train_bars=10, test_bars=5),
            timeframe=Timeframe.M1, initial_cash=_CASH,
        )


def test_walk_forward_selects_better_train_combo_for_every_window() -> None:
    n = 40
    cols = _rising_columns(n)
    combos = {"buy_hold": _buy_and_hold_signal(n), "no_trade": _no_trade_signal(n)}
    window = WalkForwardWindowSpec(train_bars=10, test_bars=5)  # step defaults to 5

    result = run_walk_forward(
        cols, combos, _config(), window, timeframe=Timeframe.M1, initial_cash=_CASH,
    )

    expected_count = (n - window.train_bars - window.test_bars) // window.effective_step_bars + 1
    assert len(result.windows) == expected_count

    first = result.windows[0]
    assert (first.train_start, first.train_end) == (0, 10)
    assert (first.test_start, first.test_end) == (10, 15)
    for w in result.windows:
        assert w.selected_combo == "buy_hold"
        assert w.train_result.final_equity > _CASH


# ================= monte_carlo.py =================


def _rising_result(n: int = 20) -> QuickBacktestResult:
    curve = tuple(_D(str(100_000 + 1_000 * i)) for i in range(n))
    return QuickBacktestResult(
        fills=(), equity_curve=curve, final_equity=curve[-1], cash=curve[-1],
        position_quantity=_D("0"), funding_cost=_D("0"), borrow_cost=_D("0"),
        bars=n, expired_orders=0, warnings=(),
    )


def test_monte_carlo_rejects_non_positive_iterations() -> None:
    with pytest.raises(MonteCarloError):
        run_monte_carlo(_rising_result(), iterations=0, rng=np.random.default_rng(0))


def test_monte_carlo_rejects_short_equity_curve() -> None:
    with pytest.raises(MonteCarloError):
        run_monte_carlo(_rising_result(1), iterations=10, rng=np.random.default_rng(0))


def test_monte_carlo_rejects_out_of_range_percentile() -> None:
    with pytest.raises(MonteCarloError):
        run_monte_carlo(
            _rising_result(), iterations=10, rng=np.random.default_rng(0), percentiles=(101,),
        )


def test_monte_carlo_rejects_zero_equity_segment() -> None:
    curve = (_D("0"), _D("100"), _D("200"))
    zeroed = QuickBacktestResult(
        fills=(), equity_curve=curve, final_equity=curve[-1], cash=curve[-1],
        position_quantity=_D("0"), funding_cost=_D("0"), borrow_cost=_D("0"),
        bars=3, expired_orders=0, warnings=(),
    )
    with pytest.raises(MonteCarloError):
        run_monte_carlo(zeroed, iterations=10, rng=np.random.default_rng(0))


def test_monte_carlo_deterministic_with_same_seed() -> None:
    base = _rising_result()
    result_a = run_monte_carlo(base, iterations=200, rng=np.random.default_rng(42))
    result_b = run_monte_carlo(base, iterations=200, rng=np.random.default_rng(42))
    assert result_a.final_equities == result_b.final_equities
    assert result_a.percentiles == result_b.percentiles


def test_monte_carlo_median_is_within_final_equity_sample_range() -> None:
    base = _rising_result()
    result = run_monte_carlo(base, iterations=500, rng=np.random.default_rng(7))
    assert len(result.final_equities) == 500
    assert min(result.final_equities) <= result.percentiles[50] <= max(result.final_equities)
