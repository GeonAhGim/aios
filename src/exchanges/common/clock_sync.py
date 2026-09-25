"""L4-11 — server-time offset correction.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

Does not call `time.time` directly; injects `clock: Callable[[], float]`
(returns epoch ms) as a kw argument (reusing the task-423 d3227c9 pattern) —
so tests can deterministically reproduce round-trip time and skew with a
fake clock.
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
        """Queries server time and updates the offset (corrected by half the
        round-trip time).

        If `abs(offset_ms)` exceeds `max_skew_ms`, the offset is still
        updated, but this raises `ExchangeError(CLOCK_SKEW)` to block the
        signing step that follows (fail-closed — a request signed while skew
        is large could be rejected by the exchange or, worse, bypass its
        time-window validation).

        `_lock` serializes the entire round trip (request -> response ->
        offset update) — if two overlapping `sync()` calls interleave, an
        older (replayed) response that started earlier but finished later
        could overwrite a more recent offset (DEPTH_L4_BR task-456 D3 audit
        finding: no guaranteed replay ordering)."""
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
