"""L40 -- unit tests for `checks/stress_capacity.run()` (§2 row 163 / §9 L40).

Spec §3.5-A hard-fail mapping row 5 (stress_capacity): any of the policy's
`required_stress` scenarios missing a result -> `VALIDATION_SCENARIO_MISSING`.
Includes D2 evidence (ADR-2026-09-09-C Decision 1): negative >= 3,
failure-injection 1, numeric performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.application.stress import REQUIRED_SCENARIOS, StressError, StressReport
from src.foundation.backtest.domain.models import BacktestConfig, BacktestMetrics, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks import stress_capacity
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.checks.stress_capacity import run
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))


def _fsm_definition_never_fires() -> dict[str, object]:
    """Same trick as test_backtest.py -- a real (non-fake) RSI condition that
    is structurally valid but can never fire (RSI is bounded to [0, 100]),
    so the real IndicatorService/TA-Lib path runs end to end deterministically
    without ever producing a trade."""
    return FSMStrategyConfig(
        strategy_id="strat-1",
        version="v1",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        initial_state=FSMState.IDLE,
        states=[FSMState.IDLE, FSMState.BUY_ORDER_PENDING],
        transitions=[
            FSMTransition(
                from_state=FSMState.IDLE,
                to_state=FSMState.BUY_ORDER_PENDING,
                condition="RSI_timeperiod14 < 0",
            ),
        ],
        author_agent="test",
    ).model_dump(mode="json")


def _bar(index: int, *, close: str = "100", volume: str = "10") -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal(volume),
        open_time=ts,
        close_time=ts,
    )


def _bars(n: int, *, volume: str = "10") -> list[Candle]:
    return [_bar(i, close=str(100 + i), volume=volume) for i in range(n)]


def _fake_metrics(
    *,
    max_drawdown_pct: Decimal = Decimal("5"),
    turnover: Decimal = Decimal("2"),
    total_trades: int = 1,
) -> BacktestMetrics:
    return BacktestMetrics(
        period_start=_T0,
        period_end=_T0 + timedelta(hours=10),
        total_return_pct=Decimal("1"),
        max_drawdown_pct=max_drawdown_pct,
        sharpe_ratio=None,
        sortino_ratio=None,
        win_rate_pct=None,
        total_trades=total_trades,
        turnover=turnover,
    )


def _ctx(
    *,
    bars: list[Candle] | None = None,
    required_stress: tuple[str, ...] = REQUIRED_SCENARIOS,
    initial_equity: Decimal = Decimal("1000"),
) -> CheckContext:
    bars = bars if bars is not None else _bars(40)
    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition=_fsm_definition_never_fires(),
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(required_stress=required_stress),
        bars=ListBars(bars),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=bars[0].open_time,
            to_time=bars[-1].close_time,
            bar_count=len(bars),
            source="bitget-rest",
            as_of=bars[-1].close_time,
        ),
        universe=UniverseSnapshot(as_of=bars[-1].close_time, members=[], snapshot_hash="b" * 64),
        config=BacktestConfig(
            strategy_id="strat-1",
            strategy_version="v1",
            initial_equity=initial_equity,
            cost_model=_COST,
            warmup_bars=5,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results={},
    )


def test_pass_when_all_required_scenarios_present() -> None:
    ctx = _ctx()
    result = run(ctx)
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.check_type == "stress_capacity"
    assert set(result.metrics["scenarios"].keys()) == set(REQUIRED_SCENARIOS)
    assert result.metrics["missing_scenarios"] == []


# -- negative -------------------------------------------------------------


def test_missing_required_scenario_triggers_hard_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    per_scenario = {name: _fake_metrics() for name in REQUIRED_SCENARIOS if name != "GAP_2PCT"}
    monkeypatch.setattr(
        stress_capacity,
        "run_stress",
        lambda *a, **k: StressReport(per_scenario=per_scenario, missing=["GAP_2PCT"]),
    )
    result = run(_ctx())
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_SCENARIO_MISSING"]
    assert result.metrics["missing_scenarios"] == ["GAP_2PCT"]


def test_multiple_missing_scenarios_all_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    present = {"COST_X2": _fake_metrics()}
    monkeypatch.setattr(
        stress_capacity,
        "run_stress",
        lambda *a, **k: StressReport(per_scenario=present, missing=[]),
    )
    result = run(_ctx())
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_SCENARIO_MISSING"]
    missing = set(result.metrics["missing_scenarios"])
    assert missing == set(REQUIRED_SCENARIOS) - {"COST_X2"}


def test_capacity_ratio_none_when_no_closed_trades(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scenario with zero closed round trips must report `capacity_ratio`
    as None (division undefined), never a silently-wrong 0 or crash."""
    per_scenario = {name: _fake_metrics(total_trades=0) for name in REQUIRED_SCENARIOS}
    monkeypatch.setattr(
        stress_capacity,
        "run_stress",
        lambda *a, **k: StressReport(per_scenario=per_scenario, missing=[]),
    )
    result = run(_ctx())
    assert result.outcome == Outcome.PASS
    for scenario_metrics in result.metrics["scenarios"].values():
        assert scenario_metrics["capacity_ratio"] is None


def test_capacity_ratio_none_when_bar_quote_volume_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with closed trades, a zero average bar quote volume (e.g. a
    corrupted/zero-volume fixture) must yield None, not a ZeroDivisionError
    or a fabricated infinite capacity."""
    per_scenario = {name: _fake_metrics(total_trades=3) for name in REQUIRED_SCENARIOS}
    monkeypatch.setattr(
        stress_capacity,
        "run_stress",
        lambda *a, **k: StressReport(per_scenario=per_scenario, missing=[]),
    )
    result = run(_ctx(bars=_bars(40, volume="0")))
    assert result.outcome == Outcome.PASS
    for scenario_metrics in result.metrics["scenarios"].values():
        assert scenario_metrics["capacity_ratio"] is None


def test_capacity_ratio_computed_from_turnover_and_bar_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Numeric correctness of the documented simplification: avg_fill_notional
    = turnover * initial_equity / (total_trades * 2); capacity_ratio =
    avg_fill_notional / avg_bar_quote_volume."""
    per_scenario = {
        name: _fake_metrics(turnover=Decimal("4"), total_trades=2) for name in REQUIRED_SCENARIOS
    }
    monkeypatch.setattr(
        stress_capacity,
        "run_stress",
        lambda *a, **k: StressReport(per_scenario=per_scenario, missing=[]),
    )
    # bars: close=100..139, volume=10 -> quote volume per bar = close*10, avg over 40 bars.
    ctx = _ctx(bars=_bars(40, volume="10"), initial_equity=Decimal("1000"))
    bars = _bars(40, volume="10")
    avg_bar_quote_volume = sum((b.close * b.volume for b in bars), Decimal("0")) / len(bars)
    expected_avg_fill_notional = (
        Decimal("4") * Decimal("1000") / (2 * 2)
    )  # turnover*equity/(trades*2)
    expected_capacity = expected_avg_fill_notional / avg_bar_quote_volume

    result = run(ctx)
    for scenario_metrics in result.metrics["scenarios"].values():
        assert scenario_metrics["capacity_ratio"] == expected_capacity


# -- D2 failure injection ---------------------------------------------------


def test_stress_error_from_run_stress_propagates_uncaught(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run()` does not catch `StressError` (unknown scenario / too few bars
    for WORST_5_DAYS_REMOVED) -- it is a replay-execution failure that
    predates policy judgement (same posture as backtest.py's
    `BacktestRunError`), so it must propagate to `run_check.py`'s own
    ERROR-evidence handling rather than being masked as a clean FAIL
    `CheckResult`."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise StressError("bars 개수가 제거할 개수 이하다")

    monkeypatch.setattr(stress_capacity, "run_stress", _boom)
    with pytest.raises(StressError):
        run(_ctx())


# -- D2 numeric performance assertion ---------------------------------------


def test_p95_latency_within_local_stress_replay_budget() -> None:
    """ADR-2026-09-09-C axis performance budget: 5 required scenarios over a
    40-bar fixture, 3 iterations, generous 3s p95 floor (mirrors
    test_stress.py's per-scenario budget reasoning, scaled up for the real
    -- not faked -- IndicatorService path this check always uses)."""
    ctx = _ctx(bars=_bars(40))
    samples: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        run(ctx)
        samples.append(time.perf_counter() - start)
    p95_seconds = max(samples)
    assert p95_seconds < 3.0, f"p95={p95_seconds * 1000:.2f}ms exceeds 3000ms budget"


# -- D2 gate-red reproduction ------------------------------------------------


def test_missing_scenario_gate_catches_policy_authority_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Red: a regression that judged "missing" against `run_stress`'s own
    `StressReport.missing` (computed against the hardcoded module constant
    `stress.REQUIRED_SCENARIOS`) instead of `ctx.policy.required_stress`
    would silently accept a policy that requires an extra scenario the
    module constant does not know about."""
    fake_report = StressReport(
        per_scenario={name: _fake_metrics() for name in REQUIRED_SCENARIOS},
        missing=[],  # the naive/regressed source of truth: looks clean
    )
    monkeypatch.setattr(stress_capacity, "run_stress", lambda *a, **k: fake_report)

    ctx = _ctx(required_stress=(*REQUIRED_SCENARIOS, "CUSTOM_SCENARIO"))

    # Red: the regressed signal (report.missing) misses the gap entirely.
    assert fake_report.missing == []

    # Green: the real check() recomputes against ctx.policy.required_stress
    # and catches the gap regardless of what StressReport.missing said.
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_SCENARIO_MISSING"]
    assert result.metrics["missing_scenarios"] == ["CUSTOM_SCENARIO"]
