"""L4-11 — venue 단위 회로차단기.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

`time.monotonic`을 직접 호출하지 않고 `clock: Callable[[], float]`을 kw
인자로 주입받는다(task-423 d3227c9 패턴 재사용) — 테스트가 가짜 시계를
전진시켜 OPEN→HALF_OPEN 전이를 실제 대기 없이 결정론적으로 검증한다.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from enum import Enum


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class VenueCircuit:
    def __init__(
        self,
        failure_threshold: int = 5,
        window_sec: float = 30.0,
        open_sec: float = 20.0,
        half_open_max: int = 2,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._window_sec = window_sec
        self._open_sec = open_sec
        self._half_open_max = half_open_max
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failure_times: list[float] = []
        self._opened_at: float | None = None
        self._half_open_attempts = 0
        self._half_open_successes = 0

    @property
    def state(self) -> CircuitState:
        return self._state

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()
        self._failure_times.clear()
        self._half_open_attempts = 0
        self._half_open_successes = 0

    def allow(self) -> bool:
        if self._state == CircuitState.CLOSED:
            return True
        if self._state == CircuitState.OPEN:
            assert self._opened_at is not None
            if self._clock() - self._opened_at < self._open_sec:
                return False
            self._state = CircuitState.HALF_OPEN
            self._half_open_attempts = 0
            self._half_open_successes = 0
        # HALF_OPEN: 시행 호출을 half_open_max개까지만 허용한다.
        if self._half_open_attempts >= self._half_open_max:
            return False
        self._half_open_attempts += 1
        return True

    def record(self, ok: bool) -> None:
        if self._state == CircuitState.HALF_OPEN:
            if not ok:
                self._open()
                return
            self._half_open_successes += 1
            if self._half_open_successes >= self._half_open_max:
                self._state = CircuitState.CLOSED
                self._failure_times.clear()
            return
        if self._state == CircuitState.CLOSED:
            if ok:
                return
            now = self._clock()
            self._failure_times = [t for t in self._failure_times if now - t < self._window_sec]
            self._failure_times.append(now)
            if len(self._failure_times) >= self._failure_threshold:
                self._open()
