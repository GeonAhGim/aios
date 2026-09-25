"""Background loop health registry — per-loop last-success time and consecutive failure count.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-08.

Same design as `metrics.py`(PLT-04) — process singleton + test swap hook
(`set_loop_health`). The `clock` defaults to `time.monotonic` but is injectable
via constructor (determinism convention from task-423/d3227c9) so stale-detection
tests can advance time with a fake clock instead of relying on `asyncio.sleep`.
`record_tick` always emits the same values to `metrics()` (or the injected port),
so this registry serves both as the in-memory state read by `/readyz`(PLT-09)
and as the source for Prometheus exposure.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from src.core.observability.metric_names import (
    LOOP_LAST_SUCCESS_AGE_SECONDS,
    LOOP_TICK_COUNT_TOTAL,
    LOOP_TICK_DURATION_SECONDS,
)
from src.core.observability.metrics import (
    MetricsPort,
    metrics,
    safe_counter,
    safe_gauge,
    safe_observe,
)

Clock = Callable[[], float]


@dataclass(frozen=True)
class LoopStatus:
    """Immutable state returned by `snapshot()` per loop name."""

    last_success_at: float | None
    consecutive_failures: int
    interval_sec: float


@dataclass
class _LoopState:
    interval_sec: float
    last_success_at: float | None = None
    consecutive_failures: int = 0


class LoopHealth:
    """Aggregates per-loop tick results and exposes `last_success_age`/`snapshot`."""

    def __init__(
        self,
        *,
        clock: Clock = time.monotonic,
        metrics_port: MetricsPort | None = None,
    ) -> None:
        self._clock = clock
        self._metrics_port = metrics_port
        self._lock = threading.Lock()
        self._states: dict[str, _LoopState] = {}

    def _port(self) -> MetricsPort:
        return self._metrics_port if self._metrics_port is not None else metrics()

    def record_tick(
        self,
        loop: str,
        ok: bool,
        duration_s: float,
        *,
        interval_sec: float | None = None,
    ) -> None:
        """Called once per tick. When `ok=False`, only increments
        `consecutive_failures` and leaves `last_success_at` untouched — stale
        detection is based on the last *success* time."""
        port = self._port()
        loop_labels = {"loop": loop}
        safe_counter(
            port, LOOP_TICK_COUNT_TOTAL, {**loop_labels, "outcome": "ok" if ok else "error"}
        )
        safe_observe(port, LOOP_TICK_DURATION_SECONDS, duration_s, loop_labels)

        with self._lock:
            state = self._states.get(loop)
            if state is None:
                state = _LoopState(interval_sec=interval_sec if interval_sec is not None else 0.0)
                self._states[loop] = state
            elif interval_sec is not None:
                state.interval_sec = interval_sec
            if ok:
                state.last_success_at = self._clock()
                state.consecutive_failures = 0
            else:
                state.consecutive_failures += 1

        age = self.last_success_age(loop)
        safe_gauge(port, LOOP_LAST_SUCCESS_AGE_SECONDS, age, loop_labels)

    def last_success_age(self, loop: str) -> float:
        """Elapsed seconds since the last success. Returns `+inf` if the loop
        has never succeeded (so the readyz `age < 3×interval` check fails)."""
        with self._lock:
            state = self._states.get(loop)
            if state is None or state.last_success_at is None:
                return float("inf")
            return self._clock() - state.last_success_at

    def snapshot(self) -> dict[str, LoopStatus]:
        with self._lock:
            return {
                name: LoopStatus(
                    last_success_at=state.last_success_at,
                    consecutive_failures=state.consecutive_failures,
                    interval_sec=state.interval_sec,
                )
                for name, state in self._states.items()
            }


_current_loop_health = LoopHealth()


def loop_health() -> LoopHealth:
    """Process singleton accessor."""
    return _current_loop_health


def set_loop_health(instance: LoopHealth) -> None:
    """Replace the singleton (test isolation — same pattern as `metrics.set_metrics`)."""
    global _current_loop_health
    _current_loop_health = instance
