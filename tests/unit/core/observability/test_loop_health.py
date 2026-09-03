"""LoopHealth 단위테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-08.
`clock`을 주입해 stale 판정을 `asyncio.sleep` 없이 결정론적으로 검증한다
(task-423/d3227c9 결정론화 관례).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from src.core.observability.loop_health import LoopHealth, LoopStatus, loop_health, set_loop_health
from src.core.observability.metric_names import (
    LOOP_LAST_SUCCESS_AGE_SECONDS,
    LOOP_TICK_COUNT_TOTAL,
    LOOP_TICK_DURATION_SECONDS,
)


@dataclass
class _SpyMetrics:
    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)
    gauges: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.gauges.append((name, value, labels))


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def advance(self, seconds: float) -> None:
        self._now += seconds

    def __call__(self) -> float:
        return self._now


def test_record_tick_success_sets_last_success_and_resets_failures() -> None:
    clock = _FakeClock(100.0)
    health = LoopHealth(clock=clock, metrics_port=_SpyMetrics())

    health.record_tick("watchdog", True, 0.01, interval_sec=5.0)

    status = health.snapshot()["watchdog"]
    assert status == LoopStatus(last_success_at=100.0, consecutive_failures=0, interval_sec=5.0)


def test_record_tick_failure_increments_failures_without_touching_last_success() -> None:
    clock = _FakeClock(0.0)
    health = LoopHealth(clock=clock, metrics_port=_SpyMetrics())

    health.record_tick("watchdog", True, 0.01, interval_sec=5.0)
    clock.advance(1.0)
    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)
    clock.advance(1.0)
    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)

    status = health.snapshot()["watchdog"]
    assert status.last_success_at == 0.0
    assert status.consecutive_failures == 2


def test_record_tick_success_after_failures_resets_consecutive_failures() -> None:
    clock = _FakeClock(0.0)
    health = LoopHealth(clock=clock, metrics_port=_SpyMetrics())

    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)
    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)
    health.record_tick("watchdog", True, 0.01, interval_sec=5.0)

    status = health.snapshot()["watchdog"]
    assert status.consecutive_failures == 0


def test_last_success_age_reflects_injected_clock_without_sleeping() -> None:
    clock = _FakeClock(0.0)
    health = LoopHealth(clock=clock, metrics_port=_SpyMetrics())

    health.record_tick("watchdog", True, 0.01, interval_sec=5.0)
    clock.advance(17.5)

    assert health.last_success_age("watchdog") == pytest.approx(17.5)


def test_last_success_age_is_infinite_when_loop_never_recorded() -> None:
    health = LoopHealth(clock=_FakeClock(0.0), metrics_port=_SpyMetrics())

    assert health.last_success_age("never_ticked") == float("inf")


def test_last_success_age_stays_infinite_when_every_tick_failed() -> None:
    clock = _FakeClock(0.0)
    health = LoopHealth(clock=clock, metrics_port=_SpyMetrics())

    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)
    clock.advance(100.0)

    assert health.last_success_age("watchdog") == float("inf")


def test_record_tick_emits_count_duration_and_last_success_age_metrics() -> None:
    clock = _FakeClock(10.0)
    spy = _SpyMetrics()
    health = LoopHealth(clock=clock, metrics_port=spy)

    health.record_tick("watchdog", True, 0.25, interval_sec=5.0)

    assert (LOOP_TICK_COUNT_TOTAL, {"loop": "watchdog", "outcome": "ok"}) in spy.counters
    assert (LOOP_TICK_DURATION_SECONDS, 0.25, {"loop": "watchdog"}) in spy.observations
    assert (LOOP_LAST_SUCCESS_AGE_SECONDS, 0.0, {"loop": "watchdog"}) in spy.gauges


def test_record_tick_failure_labels_outcome_error() -> None:
    spy = _SpyMetrics()
    health = LoopHealth(clock=_FakeClock(0.0), metrics_port=spy)

    health.record_tick("watchdog", False, 0.01, interval_sec=5.0)

    assert (LOOP_TICK_COUNT_TOTAL, {"loop": "watchdog", "outcome": "error"}) in spy.counters


def test_snapshot_is_isolated_per_loop() -> None:
    health = LoopHealth(clock=_FakeClock(0.0), metrics_port=_SpyMetrics())

    health.record_tick("watchdog", True, 0.01, interval_sec=5.0)
    health.record_tick("split_brain", False, 0.01, interval_sec=10.0)

    snapshot = health.snapshot()
    assert set(snapshot) == {"watchdog", "split_brain"}
    assert snapshot["watchdog"].consecutive_failures == 0
    assert snapshot["split_brain"].consecutive_failures == 1


def test_default_singleton_is_a_loop_health_instance() -> None:
    assert isinstance(loop_health(), LoopHealth)


def test_set_loop_health_replaces_singleton() -> None:
    original = loop_health()
    try:
        replacement = LoopHealth(clock=_FakeClock(0.0), metrics_port=_SpyMetrics())
        set_loop_health(replacement)
        assert loop_health() is replacement
    finally:
        set_loop_health(original)
