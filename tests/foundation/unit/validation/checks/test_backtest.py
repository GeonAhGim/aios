"""L38 -- unit tests for `checks/backtest.run()` (§2 row 160 / §9 L38).

Spec §3.5-A hard-fail mapping row 2 (backtest): zero cost model with
`allow_zero_cost=False` -> `VALIDATION_COST_MODEL_REQUIRED`, an I2 look-ahead
violation during replay -> `BACKTEST_LOOKAHEAD_VIOLATION`. Includes D2
evidence (ADR-2026-09-09-C Decision 1): negative >= 3, failure-injection 1,
numeric performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.application.run_backtest import run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.rules import LookaheadViolationError
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks import backtest as backtest_check
from src.foundation.validation.checks.backtest import run
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_PAID_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


def _fsm_definition_never_fires() -> dict:
    """A real (non-fake) `RSI_timeperiod14` condition that is structurally
    valid but can never be true (RSI is bounded to [0, 100]) -- this way the
    real `IndicatorService`/TA-Lib path runs end to end without ever
    producing a trade, keeping the fixture deterministic."""
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


def _bar(index: int, *, close: str = "100") -> Candle:
    ts = _T0 + timedelta(hours=index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts,
    )


def _bars(n: int) -> list[Candle]:
    return [_bar(i, close=str(100 + i)) for i in range(n)]


def _ctx(
    *,
    cost_model: CostModel = _PAID_COST,
    fsm_definition: dict | None = None,
    bars: list[Candle] | None = None,
    allow_zero_cost: bool = False,
    warmup_bars: int = 5,
) -> CheckContext:
    bars = bars if bars is not None else _bars(30)

    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition=(
                fsm_definition if fsm_definition is not None else _fsm_definition_never_fires()
            ),
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(allow_zero_cost=allow_zero_cost),
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
            initial_equity=Decimal("1000"),
            cost_model=cost_model,
            warmup_bars=warmup_bars,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results={},
    )


def test_pass_with_paid_cost_model_and_no_trades() -> None:
    bars = _bars(30)
    ctx = _ctx(bars=bars)
    result = run(ctx)
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.check_type == "backtest"
    # bars[0].close=100 -> bars[-1].close=129: (129-100)/100*100 = 29%.
    assert result.metrics["benchmark_buy_hold_return_pct"] == Decimal("29")


# -- hard fail (negative) -----------------------------------------------------


def test_zero_cost_model_triggers_validation_cost_model_required() -> None:
    ctx = _ctx(cost_model=_ZERO_COST, allow_zero_cost=False)
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_COST_MODEL_REQUIRED"]


def test_zero_cost_model_allowed_when_policy_opts_in() -> None:
    """The same zero-cost model must NOT hard-fail once the policy explicitly
    opts in (`allow_zero_cost=True`) -- confirms the gate is policy-driven,
    not an unconditional zero-cost ban."""
    ctx = _ctx(cost_model=_ZERO_COST, allow_zero_cost=True)
    result = run(ctx)
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []


def test_lookahead_violation_during_replay_triggers_hard_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*args: object, **kwargs: object) -> object:
        raise LookaheadViolationError(signal_bar_index=3, fill_bar_index=3)

    monkeypatch.setattr(backtest_check, "run_backtest", _boom)
    result = run(_ctx())
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["BACKTEST_LOOKAHEAD_VIOLATION"]


def test_malformed_fsm_definition_in_artifact_raises_instead_of_silently_passing() -> None:
    """A hand-tampered `fsm_definition` that no longer satisfies
    `FSMStrategyConfig` must fail loudly (fail-closed) -- it must never be
    silently coerced into a PASS/FAIL `CheckResult`."""
    ctx = _ctx(fsm_definition={"not": "a valid fsm definition"})
    with pytest.raises(ValidationError):
        run(ctx)


# -- D2 failure injection ------------------------------------------------------


def test_unexpected_replay_exception_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """`run()` only translates `LookaheadViolationError` into a hard fail --
    any other replay failure (e.g. a corrupted engine dependency) must
    propagate, not be masked as a clean `CheckResult`."""

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("backtest engine corrupted mid-replay")

    monkeypatch.setattr(backtest_check, "run_backtest", _boom)
    with pytest.raises(RuntimeError, match="corrupted mid-replay"):
        run(_ctx())


# -- D2 numeric performance assertion ------------------------------------------


def test_p95_latency_within_local_replay_budget() -> None:
    """ADR-2026-09-09-C axis performance budget: this check's own overhead
    (cost gate + benchmark calc) on top of `run_backtest` for a 60-bar
    replay stays within a generous 1.5s floor (mirrors
    test_run_backtest.py's 2,000-bar/1.0s budget, scaled down for this
    check's much smaller fixture)."""
    ctx = _ctx(bars=_bars(60), warmup_bars=5)
    samples: list[float] = []
    for _ in range(3):
        start = time.perf_counter()
        run(ctx)
        samples.append(time.perf_counter() - start)
    p95_seconds = max(samples)
    assert p95_seconds < 1.5, f"p95={p95_seconds * 1000:.2f}ms exceeds 1500ms budget"


# -- D2 gate-red reproduction ---------------------------------------------------


def test_cost_model_gate_catches_zero_cost_bypass_regression() -> None:
    """Red: a regression that skips `require_cost_model` and replays a
    zero-cost backtest directly would silently "succeed" with a cost-free
    (unrealistically optimistic) result instead of rejecting it."""
    ctx = _ctx(cost_model=_ZERO_COST, allow_zero_cost=False)
    bars = list(ctx.bars.upto(len(ctx.bars) - 1))
    fsm_config = FSMStrategyConfig.model_validate(ctx.artifact.fsm_definition)
    regressed_result = run_backtest(ctx.config, fsm_config, bars)  # no cost gate at all
    assert regressed_result.metrics.total_fees in (None, Decimal("0"))  # silently cost-free

    # Green: the real check() rejects it before replay ever happens.
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_COST_MODEL_REQUIRED"]
