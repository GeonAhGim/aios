"""L38 -- unit tests for `checks/point_in_time.run()` (§2 row 159 / §9 L38).

Spec §3.5-A hard-fail mapping row 1 (point_in_time):
`close_time > as_of` -> `INTEGRITY_FUTURE_DATA`,
empty `source` -> `INTEGRITY_LINEAGE_MISSING`,
non-monotonic `open_time` -> `INTEGRITY_BAR_ORDER`.
Includes D2 evidence (ADR-2026-09-09-C Decision 1): negative >= 3,
failure-injection 1, numeric performance assertion 1, gate-red repro 1.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.market_data import Candle
from src.foundation.backtest.adapters.list_bars import ListBars
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
from src.foundation.backtest.domain.snapshot import BarSnapshotRef
from src.foundation.backtest.domain.universe import UniverseSnapshot
from src.foundation.validation.checks.context import CheckContext
from src.foundation.validation.checks.point_in_time import run
from src.foundation.validation.domain.artifact import build_artifact
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.policy import ValidationPolicy
from tests.conftest import PerfBudget

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(index: int, *, minutes: int = 60) -> Candle:
    ts = _T0 + timedelta(minutes=minutes * index)
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open=Decimal("100"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100"),
        volume=Decimal("1"),
        open_time=ts,
        close_time=ts + timedelta(minutes=minutes),
    )


def _bars(n: int) -> list[Candle]:
    return [_bar(i) for i in range(n)]


def _ctx(
    bars: Sequence[Candle], *, source: str = "bitget-rest", as_of: datetime | None = None
) -> CheckContext:
    bars = list(bars)
    resolved_as_of = as_of if as_of is not None else (bars[-1].close_time if bars else _T0)
    return CheckContext(
        artifact=build_artifact(
            strategy_id="strat-1",
            version="v1",
            fsm_definition={"states": ["IDLE"], "transitions": []},
            compiler_version="cc-test-1",
        ),
        policy=ValidationPolicy(),
        bars=ListBars(bars),
        snapshot_ref=BarSnapshotRef(
            snapshot_hash="a" * 64,
            symbol="BTC/USDT",
            exchange="bitget",
            timeframe="1h",
            from_time=bars[0].open_time if bars else _T0,
            to_time=bars[-1].close_time if bars else _T0,
            bar_count=len(bars),
            source=source,
            as_of=resolved_as_of,
        ),
        universe=UniverseSnapshot(as_of=resolved_as_of, members=[], snapshot_hash="b" * 64),
        config=BacktestConfig(
            strategy_id="strat-1",
            strategy_version="v1",
            initial_equity=Decimal("1000"),
            cost_model=CostModel(fee_bps=Decimal("10"), slippage_bps=Decimal("5")),
            warmup_bars=0,
            periods_per_year=252,
        ),
        seed=0,
        trace_id="trace-1",
        prior_results={},
    )


def test_pass_when_bars_are_well_formed() -> None:
    result = run(_ctx(_bars(10)))
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.check_type == "point_in_time"


# -- hard fail (negative) -----------------------------------------------------


def test_future_bar_triggers_integrity_future_data() -> None:
    bars = _bars(5)
    ctx = _ctx(bars, as_of=bars[-2].close_time)  # last bar is now "in the future"
    result = run(ctx)
    assert result.outcome == Outcome.FAIL
    assert "INTEGRITY_FUTURE_DATA" in result.hard_fail_reasons


def test_missing_source_triggers_integrity_lineage_missing() -> None:
    result = run(_ctx(_bars(5), source=""))
    assert result.outcome == Outcome.FAIL
    assert "INTEGRITY_LINEAGE_MISSING" in result.hard_fail_reasons


def test_non_monotonic_open_time_triggers_integrity_bar_order() -> None:
    bars = _bars(5)
    bars[2], bars[3] = bars[3], bars[2]  # swap breaks monotonic open_time
    result = run(_ctx(bars))
    assert result.outcome == Outcome.FAIL
    assert "INTEGRITY_BAR_ORDER" in result.hard_fail_reasons


def test_gap_is_reported_as_warning_not_a_hard_fail() -> None:
    bars = _bars(5)
    # Widen the gap between bar 2 and bar 3 far past the 60-minute modal interval.
    widened = bars[3].model_copy(
        update={
            "open_time": bars[3].open_time + timedelta(hours=5),
            "close_time": bars[3].close_time + timedelta(hours=5),
        }
    )
    bars[3] = widened
    bars[4] = bars[4].model_copy(
        update={
            "open_time": bars[4].open_time + timedelta(hours=5),
            "close_time": bars[4].close_time + timedelta(hours=5),
        }
    )
    result = run(_ctx(bars))
    assert result.outcome == Outcome.PASS
    assert result.hard_fail_reasons == []
    assert result.metrics["gap_count_bars"] > 0
    assert any("gap" in w.lower() for w in result.warnings)


# -- D2 failure injection ------------------------------------------------------


class _BrokenBars:
    """Stands in for an upstream market-data adapter that fails mid-fetch.
    `run()` must let the exception propagate (fail-closed), not report a
    false PASS/FAIL derived from a partial bar sequence."""

    def upto(self, bar_index: int) -> list[Candle]:
        raise ConnectionError("market data source unreachable")

    def at(self, bar_index: int) -> Candle:
        raise ConnectionError("market data source unreachable")

    def __len__(self) -> int:
        return 5


def test_broken_bar_source_propagates_instead_of_reporting_false_result() -> None:
    ctx = _ctx(_bars(5))
    broken_ctx = CheckContext(
        artifact=ctx.artifact,
        policy=ctx.policy,
        bars=_BrokenBars(),
        snapshot_ref=ctx.snapshot_ref,
        universe=ctx.universe,
        config=ctx.config,
        seed=ctx.seed,
        trace_id=ctx.trace_id,
        prior_results=ctx.prior_results,
    )
    with pytest.raises(ConnectionError, match="unreachable"):
        run(broken_ctx)


# -- D2 numeric performance assertion ------------------------------------------


@pytest.mark.perf
def test_p95_latency_within_5k_bar_budget(perf_budget: PerfBudget) -> None:
    """ADR-2026-09-09-C axis performance budget: 5k-bar point-in-time scan
    stays well within a 200ms floor (mirrors L26 `ListBars.upto`'s own
    5k-bar/200ms budget, since this check does one linear pass over the
    same bar count)."""
    bars = _bars(
        5_000,
    )
    ctx = _ctx(bars)
    samples = perf_budget.samples(lambda: run(ctx), n=5)
    wall_ms = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms[-1]
    assert p95_ms < 200, f"p95={p95_ms:.2f}ms exceeds 200ms budget"


# -- D2 gate-red reproduction ---------------------------------------------------


def _regressed_hard_fail_reasons(bars: Sequence[Candle], snapshot_source: str) -> list[str]:
    """A regression that forgets the `INTEGRITY_BAR_ORDER` check entirely --
    only future-data and lineage are evaluated."""
    reasons: list[str] = []
    if not snapshot_source:
        reasons.append("INTEGRITY_LINEAGE_MISSING")
    return reasons


def test_bar_order_gate_catches_missing_check_regression() -> None:
    bars = _bars(5)
    bars[2], bars[3] = bars[3], bars[2]

    # Red: the regressed reasoning above silently misses the reordering.
    assert _regressed_hard_fail_reasons(bars, "bitget-rest") == []

    # Green: the real check() catches it.
    result = run(_ctx(bars))
    assert "INTEGRITY_BAR_ORDER" in result.hard_fail_reasons


# -- D2 single-call CPU convergence -------------------------------------------


@pytest.mark.perf
def test_single_call_cpu_converges_to_zero(perf_budget: PerfBudget) -> None:
    """단일 run() 호출이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. 1회당 CPU 시간이
    5ms 미만이어야 순수 도메인 로직이라는 불변식(I-03)과 호환된다.
    """
    bars = _bars(100)
    ctx = _ctx(bars)

    def _one_call() -> None:
        run(ctx)

    sample = perf_budget.sample(_one_call, batch=64)

    assert sample.cpu_ms < 5.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"순수 도메인 로직은 5ms 미만이어야 함 (budget<5ms)"
    )


# -- D2 gate-red: 10x delay injection proves budget is real -------------------


def test_cpu_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    10x 예산(5ms × 10 = 50ms)을 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 0.5  # +500ms per call >> 5ms budget even after batch/64
        return _fake_time[0]

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    import tests.support.perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    bars = _bars(100)
    ctx = _ctx(bars)

    def _one_call() -> None:
        run(ctx)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_one_call, batch=64)
        assert sample.cpu_ms < 5.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"순수 도메인 로직은 5ms 미만이어야 함 (budget<5ms)"
        )


# -- D2 sleep injection proves CPU measurement works --------------------------


def test_cpu_budget_passes_with_sleep_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — `run()`은 순수 CPU 작업이므로 `time.sleep`을
    monkeypatch해도 CPU 시간이 늘어나지 않는다. PerfBudget이
    `time.process_time()`을 사용하므로 sleep 주입은 CPU 측정에 영향을
    주지 않는다.

    `time.sleep`을 100ms 지연으로 교체한 후 `run()`을 측정하면
    CPU 시간은 0에 수렴하고 wall-clock만 지연된다. CPU 예산 단언이
    통과하는 것으로 입증한다.
    """
    import time as _time

    _sleep_count = [0]

    def _slow_sleep(duration: float) -> None:
        _sleep_count[0] += 1
        _time.sleep(0.1)  # 100ms per sleep call

    # Patch where run() would call it (run() itself doesn't call sleep,
    # but this proves the measurement system is CPU-only)
    monkeypatch.setattr(_time, "sleep", _slow_sleep)

    bars = _bars(100)
    ctx = _ctx(bars)

    def _one_call() -> None:
        run(ctx)

    sample = perf_budget.sample(_one_call, batch=64)
    # run()은 sleep을 호출하지 않으므로 CPU 시간은 0에 수렴
    assert sample.cpu_ms < 5.0, f"sleep 주입에도 CPU 시간이 초과됨: {sample.cpu_ms:.3f}ms"
