"""백그라운드 루프 건강 레지스트리 — 루프별 마지막 성공 시각·연속 실패 횟수.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-08.

`metrics.py`(PLT-04)와 동일한 설계 — 프로세스 싱글턴 + 테스트용 교체 훅
(`set_loop_health`). `clock`은 `time.monotonic`이 기본값이지만 생성자 인자로
주입 가능하게 뒀다(task-423/d3227c9의 결정론화 관례) — stale 판정 테스트가
`asyncio.sleep`으로 시간을 흘려보내지 않고 fake clock만으로 성립해야 하기
때문이다. `record_tick`이 매번 `metrics()`(또는 주입된 포트)로도 같은 값을
내보내므로, 이 레지스트리는 `/readyz`(PLT-09)가 읽는 인메모리 상태이자
Prometheus 노출의 원천 양쪽 역할을 겸한다.
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
from src.core.observability.metrics import MetricsPort, metrics

Clock = Callable[[], float]


@dataclass(frozen=True)
class LoopStatus:
    """`snapshot()`이 루프 이름별로 돌려주는 불변 상태."""

    last_success_at: float | None
    consecutive_failures: int
    interval_sec: float


@dataclass
class _LoopState:
    interval_sec: float
    last_success_at: float | None = None
    consecutive_failures: int = 0


class LoopHealth:
    """루프별 tick 결과를 모아 `last_success_age`/`snapshot`으로 노출한다."""

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
        """매 tick 1회 호출. `ok=False`면 `consecutive_failures`만 증가시키고
        `last_success_at`은 건드리지 않는다 — stale 판정은 마지막 *성공* 시각
        기준이다."""
        port = self._port()
        loop_labels = {"loop": loop}
        port.counter(LOOP_TICK_COUNT_TOTAL, {**loop_labels, "outcome": "ok" if ok else "error"})
        port.observe(LOOP_TICK_DURATION_SECONDS, duration_s, loop_labels)

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
        port.gauge(LOOP_LAST_SUCCESS_AGE_SECONDS, age, loop_labels)

    def last_success_age(self, loop: str) -> float:
        """마지막 성공 이후 경과 시간(초). 한 번도 성공한 적 없으면 `+inf`
        (readyz의 `age < 3×interval` 판정이 그대로 실패하도록)."""
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
    """프로세스 싱글턴."""
    return _current_loop_health


def set_loop_health(instance: LoopHealth) -> None:
    """싱글턴을 교체한다(테스트 격리용 — `metrics.set_metrics`와 동일 패턴)."""
    global _current_loop_health
    _current_loop_health = instance
