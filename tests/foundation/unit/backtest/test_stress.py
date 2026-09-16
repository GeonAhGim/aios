"""Unit tests for `backtest/application/stress.py` -- task-2411 L35 DoD."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.application import stress as stress_mod
from src.foundation.backtest.application.stress import (
    REQUIRED_SCENARIOS,
    StressError,
    run_stress,
)
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.services.condition_compiler import ORDER_FILLED

# DEEPEN(task-3204): REQUIRED_SCENARIOS 5개 x 이 픽스처(7봉) = bar-call 35개
# (최대 7봉). ADR-2026-09-09-C Decision 1 예산("백테스트 1개월 M1 1심볼 3초" =
# 약 43,200봉/3s) 중 차지할 몫은 35/43200*3s ~= 2.4ms. CI 변동 여유로 ~20배를
# 둔 50ms를 상한으로 건다.
_BUDGET_MS = 50.0
_RUN_BACKTEST_CALLS = len(REQUIRED_SCENARIOS)
_ITERATIONS = 5


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


def test_run_stress_p95_latency_within_backtest_budget_slice() -> None:
    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        run_stress(
            _config(),
            _fsm_config(),
            _bars(),
            list(REQUIRED_SCENARIOS),
            indicator_service=_FakePriceIndicatorService(),
        )
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[L35] run_stress p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


def test_run_stress_still_correct_when_run_backtest_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEPEN(task-3204) 실패 주입: run_backtest가 실제로 느려져도(회귀로
    체결 시뮬레이션에 무거운 단계가 끼어드는 상황) run_stress의 시나리오별
    결과가 지연 없는 호출과 동일한지 확인한다 -- 위 p95 단언이 실제
    run_backtest 호출을 포함한 전체 경로를 재고 있음을(캐시나 지름길이
    아님을) 보장한다."""
    original_run_backtest = stress_mod.run_backtest
    delay_s = 0.005

    def _stalled_run_backtest(*args: object, **kwargs: object) -> object:
        time.sleep(delay_s)
        return original_run_backtest(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stress_mod, "run_backtest", _stalled_run_backtest)

    started = time.perf_counter()
    stalled_report = run_stress(
        _config(),
        _fsm_config(),
        _bars(),
        list(REQUIRED_SCENARIOS),
        indicator_service=_FakePriceIndicatorService(),
    )
    elapsed_s = time.perf_counter() - started

    monkeypatch.undo()
    baseline_report = run_stress(
        _config(),
        _fsm_config(),
        _bars(),
        list(REQUIRED_SCENARIOS),
        indicator_service=_FakePriceIndicatorService(),
    )

    assert elapsed_s >= delay_s * _RUN_BACKTEST_CALLS
    assert stalled_report.per_scenario == baseline_report.per_scenario


def test_budget_gate_actually_fails_when_run_backtest_stalls_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEEPEN(task-3204) 게이트 적색 재현: run_backtest가 50ms 예산을 실제로
    넘기도록 지연을 주입하면, 위 p95 단언과 동일한 단언식이 실제로
    AssertionError를 내는지 확인한다 -- 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_run_backtest = stress_mod.run_backtest

    def _stalled_run_backtest(*args: object, **kwargs: object) -> object:
        time.sleep(_BUDGET_MS / 1000 / _RUN_BACKTEST_CALLS + 0.01)
        return original_run_backtest(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(stress_mod, "run_backtest", _stalled_run_backtest)

    samples: list[float] = []
    for _ in range(2):
        started = time.perf_counter()
        run_stress(
            _config(),
            _fsm_config(),
            _bars(),
            list(REQUIRED_SCENARIOS),
            indicator_service=_FakePriceIndicatorService(),
        )
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _BUDGET_MS


def test_scenario_failure_aborts_before_returning_partial_report() -> None:
    """DEEPEN(task-3204) 네거티브: fail-closed 검증 -- 유효한 시나리오
    (COST_X2) 뒤에 실패하는 시나리오(WORST_5_DAYS_REMOVED, bar 부족)가 오면
    이미 계산된 유효한 결과를 절반만 담아 반환하지 않고 전체가 예외로
    중단돼야 한다(CLAUDE.md 기본 포지션 = fail-closed)."""
    with pytest.raises(StressError):
        run_stress(
            _config(),
            _fsm_config(),
            _bars()[:3],
            ["COST_X2", "WORST_5_DAYS_REMOVED"],
            indicator_service=_FakePriceIndicatorService(),
        )


_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))


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
        cost_model=_COST,
        warmup_bars=0,
        periods_per_year=252,
    )


def test_all_required_scenarios_run_with_no_missing() -> None:
    report = run_stress(
        _config(),
        _fsm_config(),
        _bars(),
        list(REQUIRED_SCENARIOS),
        indicator_service=_FakePriceIndicatorService(),
    )
    assert report.missing == []
    assert set(report.per_scenario.keys()) == set(REQUIRED_SCENARIOS)


def test_cost_x3_yields_worse_return_than_cost_x2() -> None:
    report = run_stress(
        _config(),
        _fsm_config(),
        _bars(),
        ["COST_X2", "COST_X3"],
        indicator_service=_FakePriceIndicatorService(),
    )
    x2 = report.per_scenario["COST_X2"]
    x3 = report.per_scenario["COST_X3"]
    assert x2.total_trades == x3.total_trades == 1  # 같은 신호, 비용만 다르다
    assert x3.total_return_pct < x2.total_return_pct


def test_worst_days_removed_shrinks_bar_count_effect() -> None:
    """모든 bar에서 신호가 나오도록 짧은 시나리오는 아니지만, 최소한 예외 없이
    5개 미만 bar에 대해서는 fail-closed로 거부되는지 확인."""
    with pytest.raises(StressError):
        run_stress(
            _config(),
            _fsm_config(),
            _bars()[:3],
            ["WORST_5_DAYS_REMOVED"],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_unknown_scenario_is_rejected() -> None:
    with pytest.raises(StressError):
        run_stress(
            _config(),
            _fsm_config(),
            _bars(),
            ["NOT_A_SCENARIO"],
            indicator_service=_FakePriceIndicatorService(),
        )


def test_missing_lists_required_scenarios_not_run() -> None:
    report = run_stress(
        _config(),
        _fsm_config(),
        _bars(),
        ["COST_X2"],
        indicator_service=_FakePriceIndicatorService(),
    )
    assert "COST_X2" not in report.missing
    assert "GAP_2PCT" in report.missing
