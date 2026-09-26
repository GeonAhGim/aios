"""task-7631 -- self-calibrating perf budgets.

An absolute wall-clock/CPU-ms budget (``assert elapsed < 10.0``) measures the
CI runner, not the code under test: a slower or busier host fails a budget
that passes locally even though nothing regressed (GitHub run 36193686857,
verify perf serial stage). ``time.process_time()`` (as used by
``tests/conftest.py``'s ``PerfBudget``) already removes *other processes'*
CPU contention from the measurement, but it is still an absolute millisecond
figure tied to this host's clock speed.

``RelativeBudget`` removes the remaining host-speed dependency: it times a
fixed-size, pure-Python calibration loop in the *same process*, immediately
around the measured operation, and expresses the budget as a multiple
(``max_ratio``) of that calibration time instead of an absolute number. Both
measurements share the process, the host, and the moment in time, so a 2x
slower CI runner produces a 2x slower calibration *and* a 2x slower op --
the ratio stays stable. A real regression (e.g. O(n) -> O(n^2)) moves the op
time but not the calibration time, so the ratio still moves and the budget
still catches it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

_T = TypeVar("_T")

# 2,000,000 iterations of cheap int arithmetic. Large enough that its cost
# (tens of ms) sits well above Windows' ~15.6ms time.process_time() clock-tick
# quantization (see tests/conftest.py PerfBudget.sample docstring), so the
# calibration measurement itself is not noise; small enough to not add
# meaningful wall time to a test run.
_CALIBRATION_ITERATIONS = 2_000_000


def _calibration_loop() -> int:
    total = 0
    for i in range(_CALIBRATION_ITERATIONS):
        total += i * i % 7
    return total


@dataclass(frozen=True)
class RelativeSample:
    op_ms: float
    calibration_ms: float
    ratio: float


class RelativeBudget:
    """Self-calibrating replacement for an absolute-ms perf assertion.

    ``mode="cpu"`` uses ``time.process_time()`` (matches ``PerfBudget`` in
    ``tests/conftest.py``) -- pick this for pure-CPU code. ``mode="wall"``
    uses ``time.perf_counter()`` -- pick this when the measured operation
    does real I/O (disk, sleep) where ``process_time()`` would not see the
    wait time at all.
    """

    def _clock(self, mode: str) -> Callable[[], float]:
        if mode == "cpu":
            return time.process_time
        if mode == "wall":
            return time.perf_counter
        raise ValueError(f"unknown mode: {mode!r}")

    def _best_of(self, fn: Callable[[], object], *, clock: Callable[[], float], n: int) -> float:
        best: float | None = None
        for _ in range(n):
            start = clock()
            fn()
            elapsed = (clock() - start) * 1000
            if best is None or elapsed < best:
                best = elapsed
        assert best is not None
        return best

    def measure(
        self,
        fn: Callable[[], _T],
        *,
        mode: str = "cpu",
        n: int = 5,
        warmup: int = 1,
        calibration_n: int = 3,
    ) -> RelativeSample:
        clock = self._clock(mode)
        for _ in range(warmup):
            fn()
        op_ms = self._best_of(fn, clock=clock, n=n)
        calibration_ms = self._best_of(lambda: _calibration_loop(), clock=clock, n=calibration_n)
        ratio = op_ms / calibration_ms if calibration_ms else float("inf")
        return RelativeSample(op_ms=op_ms, calibration_ms=calibration_ms, ratio=ratio)

    def describe(self, sample: RelativeSample, *, max_ratio: float) -> str:
        return (
            f"op={sample.op_ms:.3f}ms calibration={sample.calibration_ms:.3f}ms "
            f"ratio={sample.ratio:.3f}x budget<{max_ratio:.3f}x"
        )

    def assert_within(
        self,
        fn: Callable[[], _T],
        *,
        max_ratio: float,
        mode: str = "cpu",
        n: int = 5,
        warmup: int = 1,
        calibration_n: int = 3,
        label: str = "",
    ) -> RelativeSample:
        sample = self.measure(fn, mode=mode, n=n, warmup=warmup, calibration_n=calibration_n)
        prefix = f"{label}: " if label else ""
        assert sample.ratio < max_ratio, prefix + self.describe(sample, max_ratio=max_ratio)
        return sample

    def p95_wall_seconds_within(
        self,
        fn: Callable[[], _T],
        *,
        max_ratio: float,
        n: int = 3,
        warmup: int = 0,
        calibration_n: int = 3,
        label: str = "",
    ) -> float:
        """For I/O-bound ops sampled a handful of times where the caller
        wants a p95 in seconds (e.g. disk-scan latency) rather than a
        best-of-N. Returns the measured p95 in seconds for the caller to log
        or assert further.

        ``warmup`` runs are executed and discarded before sampling -- useful
        for a disk scan whose first call pays a cold OS-file-cache penalty
        unrelated to the code under test; without it, a p95 over few samples
        is dominated by that one cold-cache outlier (task-7631)."""
        clock = self._clock("wall")
        for _ in range(warmup):
            fn()
        samples_ms: list[float] = []
        for _ in range(n):
            start = clock()
            fn()
            samples_ms.append((clock() - start) * 1000)
        ordered = sorted(samples_ms)
        index = max(0, -(-95 * len(ordered) // 100) - 1)
        p95_ms = ordered[index]
        calibration_ms = self._best_of(lambda: _calibration_loop(), clock=clock, n=calibration_n)
        ratio = p95_ms / calibration_ms if calibration_ms else float("inf")
        prefix = f"{label}: " if label else ""
        assert ratio < max_ratio, (
            prefix + f"p95={p95_ms:.3f}ms calibration={calibration_ms:.3f}ms "
            f"ratio={ratio:.3f}x budget<{max_ratio:.3f}x"
        )
        return p95_ms / 1000
