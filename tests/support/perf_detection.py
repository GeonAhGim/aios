"""Batched latency measurement and mutation checks using the same assertion."""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable

from tests.support.coverage_pause import paused_coverage


async def assert_async_p99_ms(
    operation: Callable[[], Awaitable[object]],
    *,
    budget_ms: float,
    samples: int = 12,
    minimum_batch: int = 8,
) -> float:
    """Measure wall latency in ms; calibrate batches above four Windows CPU ticks.

    Waiting for a lock is part of latency. CPU-only measurement cannot detect it.
    Calibration is bounded and fails closed if the clock never advances.
    The reported percentile is explicitly a percentile of batch means.
    """
    if budget_ms <= 0 or samples < 2 or minimum_batch < 2:
        raise ValueError("positive budget, multiple samples and batch >= 2 required")
    batch = minimum_batch
    with paused_coverage():
        while True:
            start = time.perf_counter()
            for _ in range(batch):
                await operation()
            elapsed = time.perf_counter() - start
            if elapsed >= 0.0625:
                break
            if batch >= 65536:
                raise AssertionError("measurement clock did not resolve a batch")
            batch = min(batch * 2, 65536)
        durations = []
        for _ in range(samples):
            start = time.perf_counter()
            for _ in range(batch):
                await operation()
            durations.append((time.perf_counter() - start) * 1000 / batch)
    if not all(math.isfinite(value) and value > 0 for value in durations):
        raise AssertionError("non-positive or non-finite latency measurement")
    p99_ms = sorted(durations)[math.ceil(0.99 * samples) - 1]
    assert p99_ms < budget_ms, f"p99={p99_ms:.6f}ms budget={budget_ms}ms batch={batch}"
    return p99_ms


async def assert_slowdown_detected(
    benchmark: Callable[[], Awaitable[object]],
) -> None:
    """Run the original benchmark after caller monkeypatches its measured operation.

    Only its budget assertion counts as detection; unrelated failures propagate.
    Call the benchmark unpatched first to establish a green control.
    """
    try:
        await benchmark()
    except AssertionError as exc:
        if "p99=" not in str(exc) or "budget=" not in str(exc):
            raise
    else:
        raise AssertionError("10x injected slowdown escaped the original budget assertion")
