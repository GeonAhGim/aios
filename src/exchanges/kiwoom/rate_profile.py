"""BR-23(task-7569) — Kiwoom call-rate profile: account type -> TokenBucket
config, split out of `auth.py` to stay under the architecture guard's 300-line
adapter-file cap (same reason `kis/rate_profile.py` is split from
`kis/oauth_client.py`).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2
VenueCapabilityProfile.rate_limits.

No confirmed published call-rate limit for Kiwoom's REST API in this session
(see `auth.py`'s module docstring for the full honesty caveat) — these are
conservative placeholders, not a cited published number (contrast
`kis/rate_profile.py`'s DOC_ONLY table, which cites KIS's own docs/community
consensus). `verified="ESTIMATED"` mirrors KIS's own "unknown -> most
conservative fallback" discipline (`kis/rate_profile.py`'s
`_CONSERVATIVE_FALLBACK`). Unlike KIS, there is no confirmed TR-domain
reference table for Kiwoom to group by, so this profile has a single bucket
per account type rather than per (account_type, tr_group).
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from src.exchanges.common.rate_limiter import TokenBucket
from src.exchanges.common.transport import RateLimitWaitObserver

Verified = Literal["LIVE_VERIFIED", "DOC_ONLY", "ESTIMATED"]


class KiwoomAccountType(str, Enum):
    REAL = "real"
    PAPER = "paper"


@dataclass(frozen=True)
class RateLimitSpec:
    rate_per_sec: float
    burst: float
    verified: Verified

    def new_bucket(
        self,
        *,
        observer: RateLimitWaitObserver | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> TokenBucket:
        """`sleep` defaults to real `asyncio.sleep`; tests inject a fake one
        (same pattern as `kis/rate_profile.py`'s `RateLimitSpec.new_bucket`) so
        the process-wide singleton bucket doesn't force a real wall-clock wait."""
        base_sleep = sleep or asyncio.sleep
        if observer is None:
            return TokenBucket(self.rate_per_sec, self.burst, sleep=base_sleep)

        async def _observed_sleep(seconds: float) -> None:
            observer.record_wait()
            await base_sleep(seconds)

        return TokenBucket(self.rate_per_sec, self.burst, sleep=_observed_sleep)


_RATE_LIMIT: dict[KiwoomAccountType, RateLimitSpec] = {
    KiwoomAccountType.REAL: RateLimitSpec(5.0, 5.0, "ESTIMATED"),
    KiwoomAccountType.PAPER: RateLimitSpec(2.0, 2.0, "ESTIMATED"),
}

# Process-wide singleton per account type -- enforces the throughput cap even
# if a caller forgets to reuse the returned bucket (same rationale as
# `kis/rate_profile.py`'s `_BUCKET_REGISTRY`).
_BUCKET_REGISTRY: dict[KiwoomAccountType, TokenBucket] = {}
_BUCKET_REGISTRY_LOCK = threading.Lock()


def build_token_bucket(
    account_type: KiwoomAccountType,
    *,
    observer: RateLimitWaitObserver | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
) -> TokenBucket:
    """Always returns the same `TokenBucket` instance for a given
    `account_type` — `observer`/`sleep` are only honored the first time a
    bucket is created for that key."""
    with _BUCKET_REGISTRY_LOCK:
        bucket = _BUCKET_REGISTRY.get(account_type)
        if bucket is None:
            bucket = _RATE_LIMIT[account_type].new_bucket(observer=observer, sleep=sleep)
            _BUCKET_REGISTRY[account_type] = bucket
        return bucket


def reset_token_bucket_registry_for_test() -> None:
    """Test-only — clears `_BUCKET_REGISTRY` so tests stay order-independent.
    Never call from production code (see `kis/rate_profile.py`'s equivalent)."""
    with _BUCKET_REGISTRY_LOCK:
        _BUCKET_REGISTRY.clear()
