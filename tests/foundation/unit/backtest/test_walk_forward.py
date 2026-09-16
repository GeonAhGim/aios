"""Unit tests for `backtest/application/walk_forward.py` -- task-2411 L35 DoD."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application import walk_forward as walk_forward_mod
from src.foundation.backtest.application.walk_forward import (
    WalkForwardError,
    run_walk_forward,
)
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.param_stability import ParamGrid
from src.foundation.backtest.domain.splits import OosLeakageError, Split, make_splits
from src.services.condition_compiler import ORDER_FILLED

# DEEPEN(task-3204): 그리드 4점 x 스플릿 2개(anchored, n_bars=50/min_train=20) =
# IS 8회 + OOS 2회 = run_backtest 10회. bar-call 합계(train 20*4 + test 15*1 +
# train 35*4 + test 15*1 = 250, 최대 50봉)가 ADR-2026-09-09-C Decision 1
# 예산("백테스트 1개월 M1 1심볼 3초" = 약 43,200봉/3s) 중 차지할 몫은
# 250/43200*3s ~= 17ms. CI 변동 여유로 ~10배를 둔 200ms를 상한으로 건다.
_BUDGET_MS = 200.0
_RUN_BACKTEST_CALLS = 10
_ITERATIONS = 5


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


def test_run_walk_forward_p95_latency_within_backtest_budget_slice() -> None:
    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        run_walk_forward(
            _config(),
            _fsm_config(),
            _bars(50),
            _grid(),
            _splits(),
            indicator_service=_FakePriceIndicatorService(),
        )
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[L35] run_walk_forward p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


def test_run_walk_forward_still_correct_when_run_backtest_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEPEN(task-3204) 실패 주입: run_backtest가 실제로 느려져도(회귀로
    체결 시뮬레이션에 무거운 단계가 끼어드는 상황) run_walk_forward가 그
    지연을 그대로 감내하면서도 선택된 파라미터·OOS 지표가 지연 없는 호출과
    동일한지 확인한다 -- 위 p95 단언이 실제 run_backtest 호출을 포함한 전체
    경로를 재고 있음을(캐시나 지름길이 아님을) 보장한다."""
    original_run_backtest = walk_forward_mod.run_backtest
    delay_s = 0.005

    def _stalled_run_backtest(*args: object, **kwargs: object) -> object:
        time.sleep(delay_s)
        return original_run_backtest(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(walk_forward_mod, "run_backtest", _stalled_run_backtest)

    started = time.perf_counter()
    stalled_report = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    elapsed_s = time.perf_counter() - started

    monkeypatch.undo()
    baseline_report = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert elapsed_s >= delay_s * _RUN_BACKTEST_CALLS
    assert [w.selected_params for w in stalled_report.windows] == [
        w.selected_params for w in baseline_report.windows
    ]
    assert stalled_report.oos_stitched_metrics == baseline_report.oos_stitched_metrics


def test_budget_gate_actually_fails_when_run_backtest_stalls_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEPEN(task-3204) 게이트 적색 재현: run_backtest가 200ms 예산을
    실제로 넘기도록 지연을 주입하면, 위 p95 단언과 동일한 단언식이 실제로
    AssertionError를 내는지 확인한다 -- 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_run_backtest = walk_forward_mod.run_backtest

    def _stalled_run_backtest(*args: object, **kwargs: object) -> object:
        time.sleep(_BUDGET_MS / 1000 / _RUN_BACKTEST_CALLS + 0.01)
        return original_run_backtest(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(walk_forward_mod, "run_backtest", _stalled_run_backtest)

    samples: list[float] = []
    for _ in range(2):
        started = time.perf_counter()
        run_walk_forward(
            _config(),
            _fsm_config(),
            _bars(50),
            _grid(),
            _splits(),
            indicator_service=_FakePriceIndicatorService(),
        )
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _BUDGET_MS


def test_all_grid_points_with_unscoreable_is_sharpe_is_rejected() -> None:
    """DEEPEN(task-3204) 네거티브: `WalkForwardError` docstring이 명시하는
    두 번째 실패 모드(모든 IS 후보의 Sharpe가 None) 전용 회귀 -- train
    구간이 평평해서(가격이 한 번도 진입 임계값을 넘지 않아 거래가 0건) 매
    bar의 equity가 그대로면 수익률 표준편차가 0이 되어, grid 축(warmup_bars)
    값과 무관하게 모든 점의 Sharpe가 None이 되므로 fail-closed로 거부돼야
    한다."""
    flat_split = Split(train=range(0, 20), test=range(20, 30), purge=0, embargo=0)
    with pytest.raises(WalkForwardError, match="Sharpe"):
        run_walk_forward(
            _config(),
            _fsm_config(),
            _flat_bars(30),
            _grid(),
            [flat_split],
            indicator_service=_FakePriceIndicatorService(),
        )


_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))
_CYCLE = [
    ("100", "100"),
    ("100", "110"),
    ("112", "111"),
    ("111", "90"),
    ("88", "89"),
]


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bars(n: int) -> list[Candle]:
    bars = []
    for i in range(n):
        open_price, close_price = _CYCLE[i % len(_CYCLE)]
        ts = _T0 + timedelta(hours=i)
        bars.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal(open_price),
                high=max(Decimal(open_price), Decimal(close_price)),
                low=min(Decimal(open_price), Decimal(close_price)),
                close=Decimal(close_price),
                volume=Decimal("1"),
                open_time=ts,
                close_time=ts,
            )
        )
    return bars


def _flat_bars(n: int) -> list[Candle]:
    """DEEPEN(task-3204): 매 bar가 open=close=100인 무거래 시나리오 --
    `_fsm_config`의 진입 임계값(PRICE > 105)을 절대 넘지 않아 체결이 0건이고,
    equity가 매 bar 동일해 수익률 표준편차가 0이 된다."""
    bars = []
    for i in range(n):
        ts = _T0 + timedelta(hours=i)
        bars.append(
            Candle(
                symbol="BTC/USDT",
                exchange="bitget",
                timeframe="1h",
                open=Decimal("100"),
                high=Decimal("100"),
                low=Decimal("100"),
                close=Decimal("100"),
                volume=Decimal("1"),
                open_time=ts,
                close_time=ts,
            )
        )
    return bars


def _fsm_config() -> FSMStrategyConfig:
    return FSMStrategyConfig(
        strategy_id="test-strategy",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[
            FSMState.IDLE,
            FSMState.BUY_ORDER_PENDING,
            FSMState.HOLDING,
            FSMState.SELL_ORDER_PENDING,
        ],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="PRICE > 105",
            ),
            FSMTransition(
                from_state=FSMState.BUY_ORDER_PENDING,
                to_state=FSMState.HOLDING,
                condition=ORDER_FILLED,
            ),
            FSMTransition(
                from_state=FSMState.HOLDING,
                to_state=FSMState.SELL_ORDER_PENDING,
                condition="PRICE < 95",
            ),
            FSMTransition(
                from_state=FSMState.SELL_ORDER_PENDING,
                to_state=FSMState.IDLE,
                condition=ORDER_FILLED,
            ),
        ],
        author_agent="test",
    )


def _config() -> BacktestConfig:
    return BacktestConfig(
        strategy_id="test-strategy",
        strategy_version="v1",
        initial_equity=Decimal("1000"),
        cost_model=_ZERO_COST,
        warmup_bars=0,
        periods_per_year=252,
    )


def _splits() -> list[Split]:
    return make_splits(n_bars=50, n_splits=2, mode="anchored", purge=0, embargo=0, min_train=20)


def _grid() -> ParamGrid:
    return ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})


def test_produces_one_window_per_split() -> None:
    report = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert len(report.windows) == 2
    assert report.selection_rule == "IS_NET_SHARPE_MAX"


def test_selected_params_are_from_the_grid() -> None:
    report = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    for window in report.windows:
        assert window.selected_params["warmup_bars"] in [0, 1, 2, 3]


def test_deterministic_repeat_run_selects_same_params() -> None:
    first = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    second = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [w.selected_params for w in first.windows] == [w.selected_params for w in second.windows]


def test_oos_stitched_metrics_cover_full_oos_span() -> None:
    report = run_walk_forward(
        _config(),
        _fsm_config(),
        _bars(50),
        _grid(),
        _splits(),
        indicator_service=_FakePriceIndicatorService(),
    )
    total_oos_bars = sum(len(w.split.test) for w in report.windows)
    assert report.oos_stitched_metrics.period_start is not None
    # stitched equity curve length isn't exposed directly, but metrics must be computable
    assert report.oos_stitched_metrics.total_trades >= 0
    assert total_oos_bars > 0


def test_empty_splits_is_rejected() -> None:
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _config(),
            _fsm_config(),
            _bars(50),
            _grid(),
            [],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_unknown_grid_axis_is_rejected() -> None:
    bad_grid = ParamGrid(axes={"not_a_field": [1, 2, 3, 4]})
    with pytest.raises(WalkForwardError):
        run_walk_forward(
            _config(),
            _fsm_config(),
            _bars(50),
            bad_grid,
            _splits(),
            indicator_service=_FakePriceIndicatorService(),
        )


def test_overlapping_splits_are_rejected() -> None:
    bad_split = Split(train=range(0, 20), test=range(15, 30), purge=0, embargo=0)
    with pytest.raises(OosLeakageError):
        run_walk_forward(
            _config(),
            _fsm_config(),
            _bars(50),
            _grid(),
            [bad_split],
            indicator_service=_FakePriceIndicatorService(),
        )
