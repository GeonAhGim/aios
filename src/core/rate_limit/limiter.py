"""L4 §9 PLT-25 — 토큰 버킷 포트 + 인메모리 구현.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-25, §10.4

`RateLimiter`는 Protocol이라 어댑터 교체가 가능하다. §10.4 미확정 사항대로
`InMemoryTokenBucket`은 단일 프로세스 전제(멀티 프로세스 배포 시 프로세스 수만큼
실효 한도가 늘어난다 — Redis 어댑터 전환은 이 리프 범위 밖)다.

`observability/metrics.py`의 `metrics()/set_metrics()` 싱글턴과 동일한 패턴으로
`limiter()/set_limiter()`를 둔다 — 미들웨어가 매 요청 이 함수를 통해 현재 구현체를
가져오므로, 통합테스트는 앱을 재구성하지 않고도 `set_limiter(...)`로 격리할 수 있다.
"""
from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from src.core.rate_limit.policy import RateLimitPolicy


class Decision(NamedTuple):
    allowed: bool
    retry_after_s: int | None
    remaining: int


class RateLimiter(Protocol):
    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision: ...


@dataclass
class _Bucket:
    tokens: float
    last_refill: float


class InMemoryTokenBucket:
    """정책·키(`(policy.name, key)`)별로 독립된 토큰 버킷을 둔다. 용량은
    `policy.limit`(버스트 허용치와 동일), 초당 리필률은 `limit / window_seconds`다
    — 짧은 시간에 `limit`개가 연속으로 몰려도 정확히 `limit`+1번째부터 거부된다
    (`test_rate_limit_storm.py`의 "121번째 read → 429" 전제).

    버킷 dict 접근은 단일 `asyncio.Lock`으로 직렬화한다 — 정책 수(5개) x 활성
    키 수 규모에서 락 경합은 무시할 만하고, 프로세스 전체에 하나뿐이라 버킷별
    락을 따로 두는 것보다 구현이 단순하다.
    """

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision:
        async with self._lock:
            bucket_key = (policy.name, key)
            now = self._clock()
            bucket = self._buckets.get(bucket_key)
            if bucket is None:
                bucket = _Bucket(tokens=float(policy.limit), last_refill=now)
                self._buckets[bucket_key] = bucket

            rate = policy.limit / policy.window_seconds
            elapsed = max(0.0, now - bucket.last_refill)
            bucket.tokens = min(float(policy.limit), bucket.tokens + elapsed * rate)
            bucket.last_refill = now

            if bucket.tokens >= 1:
                bucket.tokens -= 1
                return Decision(allowed=True, retry_after_s=None, remaining=int(bucket.tokens))

            wait_needed_s = (1 - bucket.tokens) / rate
            return Decision(
                allowed=False, retry_after_s=max(1, math.ceil(wait_needed_s)), remaining=0
            )


class UnlimitedRateLimiter:
    """`NullMetrics`(observability/metrics.py)와 같은 역할의 무제한 대역 —
    항상 허용한다. 기존 라우터 통합테스트 수백 개가 같은 프로세스 안에서
    같은 IP/subject 키를 반복 사용하므로, 실제 `InMemoryTokenBucket`을 그대로
    쓰면 이 리프 이후 그 테스트들이 서로 무관하게 429를 맞기 시작한다 —
    tests/conftest.py가 매 테스트 전후로 이걸로 되돌린다."""

    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision:
        return Decision(allowed=True, retry_after_s=None, remaining=policy.limit)


_current_limiter: RateLimiter = InMemoryTokenBucket()


def limiter() -> RateLimiter:
    """프로세스 싱글턴 rate limiter. 기본값은 `InMemoryTokenBucket`."""
    return _current_limiter


def set_limiter(port: RateLimiter) -> None:
    """싱글턴을 교체한다(테스트는 무제한 대역으로 격리 — tests/conftest.py)."""
    global _current_limiter
    _current_limiter = port
