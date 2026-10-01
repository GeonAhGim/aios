"""task-7631 -- unit tests for the RelativeBudget self-calibrating helper.

These tests prove RelativeBudget's own arithmetic and failure-injection
behavior. The wall-clock sampling test carries the perf marker so CI runs
it in the serial performance stage.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests._perf.relative_budget import RelativeBudget, RelativeSample


def test_measure_returns_positive_ratio_for_real_work() -> None:
    budget = RelativeBudget()

    def _cheap_op() -> int:
        return sum(range(1000))

    sample = budget.measure(_cheap_op, mode="cpu", n=2, warmup=1, calibration_n=1)
    assert sample.op_ms >= 0.0
    assert sample.calibration_ms > 0.0
    assert sample.ratio == pytest.approx(sample.op_ms / sample.calibration_ms)


def test_assert_within_passes_when_op_is_cheaper_than_calibration() -> None:
    budget = RelativeBudget()

    def _cheap_op() -> int:
        return 1 + 1

    budget.assert_within(_cheap_op, max_ratio=1.0, mode="cpu", n=2, calibration_n=1)


def test_assert_within_fails_when_ratio_exceeds_budget() -> None:
    """failure injection -- an op that is deliberately much slower than the
    calibration loop must push the ratio past a tiny max_ratio."""
    budget = RelativeBudget()

    def _slow_op() -> None:
        time.sleep(0.05)

    with pytest.raises(AssertionError, match="ratio="):
        budget.assert_within(_slow_op, max_ratio=0.01, mode="wall", n=1, calibration_n=1)


def test_assert_within_wall_mode_uses_perf_counter_for_sleep() -> None:
    """negative -- cpu mode (process_time) would not see time.sleep() wait
    time, so a sleeping op must be measured under wall mode to register a
    nonzero op_ms at all."""
    budget = RelativeBudget()

    def _sleep_op() -> None:
        time.sleep(0.02)

    sample = budget.measure(_sleep_op, mode="wall", n=1, calibration_n=1)
    assert sample.op_ms >= 15.0


def test_unknown_mode_raises_value_error() -> None:
    budget = RelativeBudget()
    with pytest.raises(ValueError, match="unknown mode"):
        budget.measure(lambda: None, mode="bogus")


def test_p95_wall_seconds_within_returns_seconds_and_asserts_ratio() -> None:
    budget = RelativeBudget()

    def _instant_op() -> None:
        pass

    p95_seconds = budget.p95_wall_seconds_within(_instant_op, max_ratio=1.0, n=3, calibration_n=1)
    assert p95_seconds >= 0.0


def test_p95_wall_seconds_within_fails_when_ratio_exceeds_budget() -> None:
    """failure injection -- a slow op must push the p95 ratio over budget."""
    budget = RelativeBudget()

    def _slow_op() -> None:
        time.sleep(0.05)

    with pytest.raises(AssertionError, match="p95="):
        budget.p95_wall_seconds_within(_slow_op, max_ratio=0.01, n=2, calibration_n=1)


@pytest.mark.perf
def test_p95_wall_seconds_within_warmup_calls_are_not_counted_in_samples() -> None:
    """warmup runs must execute but not be part of the measured p95 --
    otherwise a slow first (e.g. cold-cache) call would dominate a small
    sample set regardless of steady-state latency."""
    budget = RelativeBudget()
    calls: list[int] = []

    def _op() -> None:
        # A plain counter: this test asserts call *count*, not timing, so it
        # must not read a wall clock (perf marker guard, task-7434).
        calls.append(1)

    budget.p95_wall_seconds_within(_op, max_ratio=1_000_000.0, n=3, warmup=2, calibration_n=1)
    assert len(calls) == 5


async def test_measure_async_returns_positive_ratio_for_real_io() -> None:
    """task-10652 — async 변형이 real await를 재는지: `asyncio.sleep`은
    `await`를 안 거치면 측정에 안 잡혀야 한다(wall-clock만 보는 이유)."""
    budget = RelativeBudget()

    async def _io_op() -> int:
        await asyncio.sleep(0.01)
        return 1

    sample = await budget.measure_async(_io_op, n=2, warmup=1, calibration_n=1)
    assert sample.op_ms >= 5.0
    assert sample.calibration_ms > 0.0
    assert sample.ratio == pytest.approx(sample.op_ms / sample.calibration_ms)


async def test_assert_within_async_passes_when_op_is_cheaper_than_calibration() -> None:
    budget = RelativeBudget()

    async def _cheap_op() -> int:
        return 1 + 1

    await budget.assert_within_async(_cheap_op, max_ratio=1.0, n=2, calibration_n=1)


async def test_assert_within_async_fails_when_ratio_exceeds_budget() -> None:
    """failure injection -- an await that sleeps far longer than the
    calibration loop must push the ratio past a tiny max_ratio."""
    budget = RelativeBudget()

    async def _slow_op() -> None:
        await asyncio.sleep(0.05)

    with pytest.raises(AssertionError, match="ratio="):
        await budget.assert_within_async(_slow_op, max_ratio=0.01, n=1, calibration_n=1)


def test_relative_sample_is_frozen_dataclass() -> None:
    sample = RelativeSample(op_ms=1.0, calibration_ms=2.0, ratio=0.5)
    with pytest.raises(AttributeError):
        sample.op_ms = 5.0
