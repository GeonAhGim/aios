"""L4-11 — per venue/endpoint-group token bucket.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

Does not call `time.monotonic`/`asyncio.sleep` directly; injects
`clock`/`sleep` as kw arguments — so tests can supply a fake clock plus a
fake sleep that returns immediately, verifying deterministically with no
real waiting (reusing the task-423 d3227c9 pattern).
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from src.exchanges.common.error_taxonomy import ExchangeError, ExchangeErrorKind


class TokenBucket:
    def __init__(
        self,
        rate_per_sec: float,
        burst: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if rate_per_sec <= 0:
            raise ValueError(f"rate_per_sec는 양수여야 함: {rate_per_sec}")
        if burst <= 0:
            raise ValueError(f"burst는 양수여야 함: {burst}")
        self._rate = rate_per_sec
        self._burst = burst
        self._clock = clock
        self._sleep = sleep
        self._tokens = burst
        self._last_refill = clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, n: float = 1, *, timeout: float) -> None:
        """Acquires n tokens. If they cannot be acquired within timeout even by
        waiting, immediately raises `ExchangeError(RATE_LIMITED,
        retryable=True)` (this does not actually wait the full timeout before
        failing -- it precomputes the needed wait time and fails fast).

        `_lock` serializes the entire call -- if concurrent callers each read
        the remaining token count before waiting and deduct it after waiting
        without re-verifying (clamped to 0), they could double-consume the
        remaining balance with another waiting caller and issue more than the
        burst limit allows (TOCTOU race, DEPTH_L4_BR task-456 D3 audit
        finding)."""
        async with self._lock:
            if n > self._burst:
                raise ExchangeError(
                    ExchangeErrorKind.RATE_LIMITED,
                    message=f"요청 토큰 {n}개가 버스트 한도 {self._burst}개를 초과함",
                )
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return
            wait_needed = (n - self._tokens) / self._rate
            if wait_needed > timeout:
                raise ExchangeError(
                    ExchangeErrorKind.RATE_LIMITED,
                    message=f"rate limit 대기시간 {wait_needed:.3f}s가 timeout {timeout}s 초과",
                )
            await self._sleep(wait_needed)
            self._refill()
            self._tokens = max(0.0, self._tokens - n)
