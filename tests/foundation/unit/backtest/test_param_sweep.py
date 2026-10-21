"""Unit tests for `backtest/application/param_sweep.py` -- task-2411 L35 DoD."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast

import pytest

import src.foundation.backtest.application.param_sweep as param_sweep_module
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application.param_sweep import SweepError, SweepResult, sweep
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.param_stability import ParamGrid, ParamStabilityError
from src.services.condition_compiler import ORDER_FILLED
from tests.conftest import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


@dataclass
class _FakeIndicatorResult:
    values: list[float | None]


class _FakePriceIndicatorService:
    def calculate(
        self, indicator: str, candles: list[Candle], **params: int
    ) -> _FakeIndicatorResult:
        assert indicator == "PRICE"
        return _FakeIndicatorResult(values=[float(candles[-1].close)])


def _bar(*, open_price: str, close_price: str, index: int) -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
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


def _bars() -> list[Candle]:
    return [
        _bar(index=0, open_price="100", close_price="100"),
        _bar(index=1, open_price="100", close_price="110"),
        _bar(index=2, open_price="112", close_price="111"),
        _bar(index=3, open_price="111", close_price="90"),
        _bar(index=4, open_price="88", close_price="89"),
        _bar(index=5, open_price="90", close_price="91"),
        _bar(index=6, open_price="91", close_price="92"),
    ]


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


def _grid() -> ParamGrid:
    return ParamGrid(axes={"warmup_bars": [0, 1, 2, 3]})


def test_sweep_runs_one_point_per_grid_point_in_order() -> None:
    result = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        n_blocks=2,
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [p.point for p in result.points] == [(0,), (1,), (2,), (3,)]
    assert len(result.perf_matrix) == 4
    assert all(len(row) == 2 for row in result.perf_matrix)


# -- D2 CPU measurement convergence: single sweep call cpu_ms → 0 for trivial work ---


@pytest.mark.perf
def test_sweep_cpu_ms_converges_to_zero_for_trivial_grid(perf_budget: PerfBudget) -> None:
    """CPU measurement invariant: a 4-point grid sweep over 7 bars with a fake
    indicator service must report cpu_ms close to zero.

    This verifies that ``PerfSample.cpu_ms`` actually measures process CPU time
    (via ``time.process_time()``) and does not converge to a non-zero floor
    caused by measurement artefacts or batch=1 quantization on Windows.
    """
    grid = _grid()

    def _trivial_sweep() -> SweepResult:
        return sweep(
            _config(),
            _fsm_config(),
            _bars(),
            grid,
            indicator_service=_FakePriceIndicatorService(),
        )

    # batch=1 is the stress case: Windows time.process_time() has ~15.6ms tick
    # resolution; a single trivial call should still read 0ms or 1 tick.
    samples = perf_budget.samples(_trivial_sweep, n=5, warmup=1, batch=1)
    max_cpu_ms = max(s.cpu_ms for s in samples)
    assert max_cpu_ms < 50.0, (
        f"sweep cpu_ms too high for trivial work: {max_cpu_ms:.2f}ms "
        f"(expected <50ms, batch=1 quantization floor)"
    )


# -- D2 failure injection: 10x budget CPU injection → throughput assertion red --


@pytest.mark.perf
def test_throughput_assertion_fires_on_10x_cpu_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Budget invariance (D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산을 초과하는 CPU 시간을 가장하면 budget 단언이 AssertionError로
    적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import tests.support.perf_budget as _pb_module

    _THROUGHPUT_FLOOR = 10.0  # same floor as test_sweep_throughput_meets_minimum_points_per_second
    _fake_cpu = [0.0]

    def _fake_process_time() -> float:
        _fake_cpu[0] += 0.5  # +500ms per call >> generous baseline
        return _fake_cpu[0]

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    grid = _grid()

    def _run_once() -> SweepResult:
        return sweep(
            _config(),
            _fsm_config(),
            _bars(),
            grid,
            indicator_service=_FakePriceIndicatorService(),
        )

    sample = perf_budget.best_of(_run_once)
    result = cast(SweepResult, sample.result)
    cpu_seconds = sample.cpu_ms / 1000
    throughput = len(result.points) / cpu_seconds if cpu_seconds > 0 else float("inf")
    # Same assertion as the green test; with 10× CPU injection it MUST exceed budget.
    with pytest.raises(AssertionError) as exc_info:
        assert throughput > _THROUGHPUT_FLOOR, (
            f"grid-point throughput too low: {throughput:.2f} points/sec"
        )
    assert "throughput" in str(exc_info.value)


# -- D2 negative tests: sweep edge cases ---------------------------------------


def test_empty_axes_rejected_by_param_grid() -> None:
    """ParamGrid rejects empty axes — fail-fast at grid construction, not sweep."""
    from src.foundation.backtest.domain.param_stability import ParamStabilityError

    with pytest.raises(ParamStabilityError, match="axes must not be empty"):
        ParamGrid(axes={})


def test_duplicate_axis_values_rejected() -> None:
    """ParamGrid rejects duplicate axis values — uniqueness invariant."""
    with pytest.raises(ParamStabilityError, match="strictly ascending"):
        ParamGrid(axes={"warmup_bars": [0, 0, 1, 2]})


def test_unsorted_axis_values_rejected() -> None:
    """ParamGrid rejects unsorted axis values — order invariant."""
    with pytest.raises(ParamStabilityError, match="strictly ascending"):
        ParamGrid(axes={"warmup_bars": [3, 0, 1, 2]})
    first = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        indicator_service=_FakePriceIndicatorService(),
    )
    second = sweep(
        _config(),
        _fsm_config(),
        _bars(),
        _grid(),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert [p.metrics for p in first.points] == [p.metrics for p in second.points]


def test_unknown_grid_axis_is_rejected() -> None:
    bad_grid = ParamGrid(axes={"not_a_field": [1, 2, 3, 4]})
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            bad_grid,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_too_many_blocks_for_equity_curve_is_rejected() -> None:
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            n_blocks=100,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_zero_n_blocks_is_rejected() -> None:
    """n_blocks < 1 is not a valid slicing of an equity curve -- fail-closed input."""
    with pytest.raises(SweepError):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            n_blocks=0,
            indicator_service=_FakePriceIndicatorService(),
        )


def test_run_backtest_failure_propagates_without_partial_result(monkeypatch) -> None:
    """A dependency failure (`run_backtest` raising) must not be swallowed into a
    partial/empty `SweepResult` -- fail-closed, not fail-open."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("run_backtest dependency failure (injected)")

    monkeypatch.setattr(param_sweep_module, "run_backtest", _boom)
    with pytest.raises(RuntimeError, match="injected"):
        sweep(
            _config(),
            _fsm_config(),
            _bars(),
            _grid(),
            indicator_service=_FakePriceIndicatorService(),
        )


@pytest.mark.perf
def test_sweep_throughput_meets_minimum_points_per_second(perf_budget: PerfBudget) -> None:
    """Numeric performance assertion (D2 floor, ADR-2026-09-09-C): a 4-point grid over
    7 bars with a fake indicator service must clear a generous throughput floor -- this
    guards against an accidental O(n^2) regression in the grid-point loop, not a tight
    production SLA. task-10947: routed through the shared `perf_budget` (process_time
    based) instead of raw `time.perf_counter()` so other workers/local-LLM load on this
    host do not get counted as elapsed sweep time."""
    grid = _grid()

    def _run_once() -> SweepResult:
        return sweep(
            _config(),
            _fsm_config(),
            _bars(),
            grid,
            indicator_service=_FakePriceIndicatorService(),
        )

    sample = perf_budget.best_of(_run_once)
    result = cast(SweepResult, sample.result)
    cpu_seconds = sample.cpu_ms / 1000
    throughput = len(result.points) / cpu_seconds if cpu_seconds > 0 else float("inf")
    assert throughput > 10.0, f"grid-point throughput too low: {throughput:.2f} points/sec"
