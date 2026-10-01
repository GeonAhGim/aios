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

esc-ci-pytest_latency_serial -- the ``pytest_latency_serial`` CI step runs
with ``--cov=src --cov-append``. coverage.py's global line tracer
(``sys.settrace``) does not distribute its per-line overhead evenly: the
calibration loop is one line executed 2,000,000 times (low trace overhead
per unit of work), while the measured op (e.g. a 1000-decl parse) touches
many distinct lines and call frames (high trace overhead per unit of work).
Measured locally, this alone inflates ``op_ms/calibration_ms`` from 0.80x to
1.27x with no code change -- on a slower/busier CI runner that inflation
pushes real ``max_ratio`` budgets over the edge (esc-ci-pytest_latency_serial
partial output ``.FFF``, bisect landed on an unrelated commit because the
failure is host-load-dependent, not code-dependent). ``_best_of`` pauses the
active ``coverage.Coverage`` instance (same helper ``tests/conftest.py``
``PerfBudget.sample`` already uses for task-7253/7250) around every timed
call so neither side of the ratio carries tracer overhead.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from tests.conftest import paused_coverage

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
            with paused_coverage():
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
        calibration_fn: Callable[[], object] | None = None,
    ) -> RelativeSample:
        """`calibration_fn` defaults to the fixed pure-CPU loop (host-speed
        calibration). Pass a different reference operation when the thing
        under test is *not* CPU-bound but still has a real, in-process,
        same-moment comparison available -- e.g. a single DB round-trip when
        measuring N concurrent round-trips, so the ratio tracks the actual
        contended resource (DB server load) instead of this process's CPU
        speed, which may not move in lockstep with it (task-10652)."""
        clock = self._clock(mode)
        for _ in range(warmup):
            fn()
        op_ms = self._best_of(fn, clock=clock, n=n)
        calibration_ms = self._best_of(
            calibration_fn or _calibration_loop, clock=clock, n=calibration_n
        )
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
        calibration_fn: Callable[[], object] | None = None,
        label: str = "",
    ) -> RelativeSample:
        sample = self.measure(
            fn,
            mode=mode,
            n=n,
            warmup=warmup,
            calibration_n=calibration_n,
            calibration_fn=calibration_fn,
        )
        prefix = f"{label}: " if label else ""
        assert sample.ratio < max_ratio, prefix + self.describe(sample, max_ratio=max_ratio)
        return sample

    async def _best_of_async(
        self, fn: Callable[[], Awaitable[object]], *, clock: Callable[[], float], n: int
    ) -> float:
        best: float | None = None
        for _ in range(n):
            with paused_coverage():
                start = clock()
                await fn()
                elapsed = (clock() - start) * 1000
            if best is None or elapsed < best:
                best = elapsed
        assert best is not None
        return best

    async def measure_async(
        self,
        fn: Callable[[], Awaitable[_T]],
        *,
        n: int = 3,
        warmup: int = 1,
        calibration_n: int = 3,
    ) -> RelativeSample:
        """`measure()`의 async 변형 — 네트워크/DB 왕복처럼 `await`가 필요한
        작업을 ``time.perf_counter()`` 벽시계로 재되, 같은 프로세스에서 같은
        순간에 돈 CPU 보정 루프와의 비율로 호스트 부하를 상쇄한다(모듈
        docstring 참고). 보정 루프 자체는 동기 CPU 작업이라 `await`가 필요
        없다."""
        clock = self._clock("wall")
        for _ in range(warmup):
            await fn()
        op_ms = await self._best_of_async(fn, clock=clock, n=n)
        calibration_ms = self._best_of(lambda: _calibration_loop(), clock=clock, n=calibration_n)
        ratio = op_ms / calibration_ms if calibration_ms else float("inf")
        return RelativeSample(op_ms=op_ms, calibration_ms=calibration_ms, ratio=ratio)

    async def assert_within_async(
        self,
        fn: Callable[[], Awaitable[_T]],
        *,
        max_ratio: float,
        n: int = 3,
        warmup: int = 1,
        calibration_n: int = 3,
        label: str = "",
    ) -> RelativeSample:
        sample = await self.measure_async(fn, n=n, warmup=warmup, calibration_n=calibration_n)
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
