"""L4-11 — venue·엔드포인트 그룹별 토큰 버킷.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

`time.monotonic`/`asyncio.sleep`을 직접 호출하지 않고 `clock`/`sleep`을 kw
인자로 주입받는다 — 테스트가 가짜 시계+즉시 반환하는 가짜 sleep을 넣어
실제 대기 없이 결정론적으로 검증할 수 있다(task-423 d3227c9 패턴 재사용).
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

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last_refill)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, n: float = 1, *, timeout: float) -> None:
        """토큰 n개를 확보한다. 대기해도 timeout 내 확보 불가능하면
        `ExchangeError(RATE_LIMITED, retryable=True)`를 즉시 발생시킨다
        (실제로 timeout만큼 기다린 뒤 실패시키지 않는다 — 필요한 대기시간을
        미리 계산해 fail-fast한다)."""
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
