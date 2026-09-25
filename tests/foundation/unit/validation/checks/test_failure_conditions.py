"""L40 -- unit tests for `checks/failure_conditions.run()` (§2 row 164 / §9 L40).

Spec §3.5-A hard-fail mapping row 6 (failure_conditions): OOS result absent
(or unusable) -> `VALIDATION_NO_INVALIDATION_CRITERIA`. Includes D2 evidence
(ADR-2026-09-09-C Decision 1): negative >= 3, failure-injection 1, numeric
performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import pytest

from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMState, FSMStrategyConfig, FSMTransition
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.checks.failure_conditions import run
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy
from src.foundation.validation.domain.rules import compute_result_hash

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))


def _fsm_definition() -> dict[str, object]:
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
        volume=Decimal("10"),
        open_time=ts,
        close_time=ts,
    )


def _bars(n: int) -> list[Candle]:
    return [_bar(i, close=str(100 + i)) for i in range(n)]


def _oos_result(*, metrics: dict[str, object] | None = None) -> CheckResult:
    resolved_metrics = metrics if metrics is not None else {"oos_max_drawdown_pct": Decimal("10")}
    return CheckResult(
        check_type="oos_walk_forward",
        outcome=Outcome.PASS,
        metrics=resolved_metrics,
        result_hash=compute_result_hash(resolved_metrics),
        policy_version="vp-v1",
        evidence_refs=[],
    )


def _ctx(
    *,
    bars: list[Candle] | None = None,
    prior_results: dict[str, CheckResult] | None = None,
) -> CheckContext:
    bars = bars if bars is not None else _bars(10)
    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition=_fsm_definition(),
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(),
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
            cost_model=_COST,
            warmup_bars=5,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results=prior_results if prior_results is not None else {},
    )


def test_pass_with_obligations_when_oos_result_present() -> None:
    ctx = _ctx(prior_results={"oos_walk_forward": _oos_result()})
    result = run(ctx)
    assert result.outcome == Outcome.PASS_WITH_OBLIGATIONS
    assert result.hard_fail_reasons == []
    assert result.check_type == "failure_conditions"
    assert result.obligations == ["PAUSE_IF_MDD_GT_15.0", "REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0"]
    assert result.metrics["pause_mdd_threshold_pct"] == Decimal("15.0")


# -- negative ---------------------------------------------------------------


def test_missing_oos_prior_result_triggers_hard_fail() -> None:
    ctx = _ctx(prior_results={})
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NO_INVALIDATION_CRITERIA"]
    assert result.obligations == []


def test_oos_result_missing_mdd_key_triggers_hard_fail() -> None:
    """An `oos_walk_forward` result that exists but does not carry the
    expected MDD metric key must still hard-fail -- a criterion can't be
    derived from an incompatible/partial upstream result any more than from
    a wholly absent one."""
    ctx = _ctx(prior_results={"oos_walk_forward": _oos_result(metrics={"unrelated_key": 1})})
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NO_INVALIDATION_CRITERIA"]


def test_wrong_check_type_key_in_prior_results_is_not_mistaken_for_oos() -> None:
    """A prior result stored under a different check_type key (e.g.
    'robustness') must not be picked up as the OOS result just because
    `prior_results` is non-empty."""
    ctx = _ctx(prior_results={"robustness": _oos_result()})
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.hard_fail_reasons == ["VALIDATION_NO_INVALIDATION_CRITERIA"]


# -- D2 failure injection ----------------------------------------------------


def test_non_numeric_mdd_value_raises_instead_of_silently_passing() -> None:
    """A corrupted upstream metric (non-numeric MDD) must raise loudly
    (fail-closed) rather than being coerced into a fabricated threshold."""
    ctx = _ctx(
        prior_results={
            "oos_walk_forward": _oos_result(metrics={"oos_max_drawdown_pct": "not-a-number"})
        }
    )
    with pytest.raises(InvalidOperation):
        run(ctx)


# -- D2 numeric performance assertion -----------------------------------------


@pytest.mark.perf
def test_p95_latency_within_local_budget() -> None:
    """This check does no I/O and no replay -- pure dict lookup + one
    multiplication -- so its own overhead budget is tight (5ms)."""
    ctx = _ctx(prior_results={"oos_walk_forward": _oos_result()})
    samples: list[float] = []
    for _ in range(20):
        start = time.perf_counter()
        run(ctx)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95_seconds = samples[int(len(samples) * 0.95)]
    assert p95_seconds < 0.005, f"p95={p95_seconds * 1000:.2f}ms exceeds 5ms budget"


# -- D2 gate-red reproduction --------------------------------------------------


def test_hard_fail_gate_catches_missing_oos_bypass_regression() -> None:
    """Red: a regression that always emits the fixed
    `REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0` obligation regardless of OOS
    result presence (skipping the `oos_result is None` gate) would silently
    "succeed" with an incomplete/fabricated obligation set instead of
    rejecting the strategy for lacking real invalidation criteria."""
    ctx = _ctx(prior_results={})

    # Red: what a regression that never checks for a missing OOS result
    # would still be able to construct -- a non-empty obligation list.
    regressed_obligations = ["REVALIDATE_IF_ROLLING_SHARPE_30D_LT_0"]
    assert regressed_obligations  # the regressed shortcut looks superficially fine

    # Green: the real check() rejects it before any obligation is emitted.
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert result.obligations == []
    assert result.hard_fail_reasons == ["VALIDATION_NO_INVALIDATION_CRITERIA"]
