"""L4 §9 PLT-25 — Token bucket port + in-memory implementation.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-25, §10.4

`RateLimiter` is a Protocol, so adapter replacement is possible. As noted in §10.4
(undecided), `InMemoryTokenBucket` assumes a single process (in a multi-process
deployment the effective limit scales with the number of processes — switching to a
Redis adapter is out of scope for this leaf).

Following the same pattern as `metrics()/set_metrics()` in `observability/metrics.py`,
`limiter()/set_limiter()` provide a singleton accessor — the middleware fetches the
current implementation via this function on every request, so integration tests can
isolate behavior with `set_limiter(...)` without restructuring the app.
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
    """Maintains an independent token bucket per policy·key (`(policy.name, key)`).
    Capacity equals `policy.limit` (same as the burst allowance), and the refill rate
    is `limit / window_seconds` per second — even if `limit` requests arrive in rapid
    succession, exactly the `limit`+1th request is rejected (assumption in
    `test_rate_limit_storm.py`: "121st read → 429").

    Bucket dict access is serialized through a single `asyncio.Lock` — at the scale of
    ~5 policies × active keys, lock contention is negligible, and having one lock for
    the entire process is simpler than per-bucket locks.
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
    """Unlimited band — serves the same role as `NullMetrics` (observability/metrics.py).
    Always allows. Hundreds of existing router integration tests reuse the same
    IP/subject keys within the same process, so using the real `InMemoryTokenBucket`
    would cause those tests to start failing with 429s unrelated to each other —
    tests/conftest.py restores this limiter before and after each test.
    """

    async def acquire(self, policy: RateLimitPolicy, key: str) -> Decision:
        return Decision(allowed=True, retry_after_s=None, remaining=policy.limit)


_current_limiter: RateLimiter = InMemoryTokenBucket()


def limiter() -> RateLimiter:
    """Process-singleton rate limiter. Default is `InMemoryTokenBucket`."""
    return _current_limiter


def set_limiter(port: RateLimiter) -> None:
    """Replace the singleton (tests swap to unlimited band for isolation — tests/conftest.py)."""
    global _current_limiter
    _current_limiter = port
