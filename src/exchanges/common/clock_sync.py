"""L4-11 — 서버시간 오프셋 보정.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

`time.time`을 직접 호출하지 않고 `clock: Callable[[], float]`(epoch ms
반환)을 kw 인자로 주입받는다(task-423 d3227c9 패턴 재사용) — 테스트가
가짜 시계로 왕복시간·skew를 결정론적으로 재현한다.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind

DEFAULT_MAX_SKEW_MS = 1000


def _default_clock_ms() -> float:
    return time.time() * 1000


class ServerClock:
    def __init__(
        self,
        *,
        max_skew_ms: int = DEFAULT_MAX_SKEW_MS,
        clock: Callable[[], float] = _default_clock_ms,
    ) -> None:
        self._max_skew_ms = max_skew_ms
        self._clock = clock
        self._offset_ms = 0.0
        self._last_sync_at: float | None = None
        self._lock = asyncio.Lock()

    @property
    def offset_ms(self) -> float:
        return self._offset_ms

    @property
    def last_sync_at(self) -> float | None:
        return self._last_sync_at

    async def sync(self, fetch_server_ms: Callable[[], Awaitable[int]]) -> None:
        """서버시간을 조회해 오프셋을 갱신한다(왕복시간 절반 보정).

        `abs(offset_ms)`가 `max_skew_ms`를 넘으면 오프셋은 갱신한 채로
        `ExchangeError(CLOCK_SKEW)`를 발생시켜 이후 서명 단계를 차단한다
        (fail-closed — 스큐가 큰 상태로 서명된 요청은 거래소가 거부하거나
        더 나쁘게는 시간창 검증을 우회할 수 있다).

        `_lock`으로 전체 왕복(요청→응답→오프셋 갱신)을 직렬화한다 — 겹쳐
        호출된 두 `sync()`가 인터리빙되면, 먼저 시작했지만 나중에 끝난
        오래된(리플레이된) 응답이 더 최근 오프셋을 덮어쓸 수 있다
        (DEPTH_L4_BR task-456 D3 감사 발견: replay 순서 보장 부재)."""
        async with self._lock:
            t0 = self._clock()
            server_ms = await fetch_server_ms()
            t1 = self._clock()
            round_trip_ms = max(0.0, t1 - t0)
            estimated_server_now_ms = server_ms + round_trip_ms / 2
            self._offset_ms = estimated_server_now_ms - t1
            self._last_sync_at = t1
            if abs(self._offset_ms) > self._max_skew_ms:
                raise ExchangeError(
                    ExchangeErrorKind.CLOCK_SKEW,
                    retryable=False,
                    message=f"서버시간 오프셋 {self._offset_ms:.1f}ms가 "
                    f"max_skew_ms={self._max_skew_ms} 초과",
                )

    def now_ms(self) -> int:
        return round(self._clock() + self._offset_ms)
