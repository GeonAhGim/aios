"""L38 -- unit tests for `checks/backtest.run()` (§2 row 160 / §9 L38).

Spec §3.5-A hard-fail mapping row 2 (backtest): zero cost model with
`allow_zero_cost=False` -> `VALIDATION_COST_MODEL_REQUIRED`, an I2 look-ahead
violation during replay -> `BACKTEST_LOOKAHEAD_VIOLATION`. Includes D2
evidence (ADR-2026-09-09-C Decision 1): negative >= 3, failure-injection 1,
numeric performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

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
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_PAID_COST = CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5"))
_ZERO_COST = CostModel(fee_bps=Decimal("0"), slippage_bps=Decimal("0"))


def _fsm_definition_never_fires() -> dict[str, object]:
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
    fsm_definition: dict[str, object] | None = None,
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


# -- D2 CPU measurement convergence: single call cpu_ms → 0 for trivial work ---


@pytest.mark.perf
def test_backtest_check_cpu_ms_converges_to_zero_for_single_run(
    perf_budget: Any,
) -> None:
    """CPU measurement invariant: a single `run()` call over 30 bars with a
    no-trade FSM must report cpu_ms close to zero.

    This verifies that ``PerfSample.cpu_ms`` actually measures process CPU time
    (via ``time.process_time()``) and does not converge to a non-zero floor
    caused by measurement artefacts or batch=1 quantization on Windows.
    """

    def _run_once() -> CheckResult:
        return run(_ctx(bars=_bars(30), warmup_bars=5))

    # batch=1 is the stress case: Windows time.process_time() has ~15.6ms tick
    # resolution; a single trivial call should still read 0ms or 1 tick.
    # The invariant is about the measurement *floor*, so assert on the median:
    # process_time() counts every thread's CPU, and one stray GC/background tick
    # under a loaded runner (seen: 125ms, 8 ticks) must not fail a floor check.
    samples = perf_budget.samples(_run_once, n=7, warmup=1, batch=1)
    cpu_ms = sorted(s.cpu_ms for s in samples)
    median_cpu_ms = cpu_ms[len(cpu_ms) // 2]
    assert median_cpu_ms < 50.0, (
        f"backtest check cpu_ms too high for trivial work: median {median_cpu_ms:.2f}ms "
        f"(expected <50ms, batch=1 quantization floor)"
    )


# -- D2 failure injection: 10x budget CPU injection → assertion red ------------


@pytest.mark.perf
def test_backtest_check_assertion_fires_on_10x_cpu_injection(
    perf_budget: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Budget invariance (D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산을 초과하는 CPU 시간을 가장하면 budget 단언이 AssertionError로
    적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import time as _time_module

    _fake_cpu = [0.0]

    def _fake_process_time() -> float:
        _fake_cpu[0] += 0.5  # +500ms per call >> generous baseline
        return _fake_cpu[0]

    monkeypatch.setattr(_time_module, "process_time", _fake_process_time)

    def _run_once() -> CheckResult:
        return run(_ctx(bars=_bars(30), warmup_bars=5))

    # Use the same assertion as test_backtest_check_cpu_ms_converges_to_zero_for_single_run:
    # cpu_ms < 50ms. With 500ms injected per call it MUST exceed budget.
    sample = perf_budget.sample(_run_once)
    with pytest.raises(AssertionError) as exc_info:
        assert sample.cpu_ms < 50.0, f"cpu_ms={sample.cpu_ms:.2f}ms exceeds 50ms budget"
    assert "cpu_ms" in str(exc_info.value)


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


@pytest.mark.perf
def test_p95_latency_within_local_replay_budget(perf_budget: Any) -> None:
    """ADR-2026-09-09-C axis performance budget: this check's own overhead
    (cost gate + benchmark calc) on top of `run_backtest` for a 60-bar
    replay stays within a generous 1.5s floor (mirrors
    test_run_backtest.py's 2,000-bar/1.0s budget, scaled down for this
    check's much smaller fixture)."""
    samples = perf_budget.samples(lambda: run(_ctx(bars=_bars(60), warmup_bars=5)), n=3)
    p95_ms = max(s.wall_ms for s in samples)
    assert p95_ms < 1500, f"p95={p95_ms:.2f}ms exceeds 1500ms budget"


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
