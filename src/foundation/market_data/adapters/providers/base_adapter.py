"""DC-11 — `MarketDataProvider` SPI common base: rate-limit token bucket, retry,
normalization hooks.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2 module table DC-11, §9.2 DC-11 (prerequisite DC-5, task-1126 554f078 merged).

Does NOT override `MarketDataProvider` Protocol (`ports/provider.py`) or
`ProviderCapabilities` (task-1187 decision) — this class provides only the
common plumbing that DC-12 vendor adapters (bitget/kis) inherit to implement
that Protocol. Does not touch any existing `src/exchanges/**` paths
(except importing from them).

Reuses the L4-11 durability modules (`src/exchanges/common/rate_limiter.py`,
`http_policy.py`, task-456 b638afc) for token bucket and retry mechanisms
without creating a new taxonomy. Exceptions crossing the SPI boundary are
unified under DC-5's `DataProviderError`/`DataProviderErrorCode` — if
`ExchangeError` leaks into the calling layer (application), it would handle
exceptions outside the 4-category taxonomy defined by DC-5, so the token
bucket's `ExchangeError` is translated here immediately to
`DataProviderError(DATA_PROVIDER_RATE_LIMITED)`.

Time and randomness are fully injected (`clock`/`sleep`/`rng`) — never calls
`time.monotonic`/`asyncio.sleep`/`random.random` directly, allowing tests to
verify deterministically without real waits (same pattern as L4-11, inherited
from task-423 d3227c9).

Retry scope — only retries when `DataProviderError.retryable` (determined by
code, `_RETRYABLE_CODES` in `ports/provider.py`) is True. Permanent/permission
errors (`DATA_ENTITLEMENT_DENIED`, `DATA_COVERAGE_MISSING`) and unknown
exceptions that are not `DataProviderError` are propagated immediately (no
infinite retries — same fail-closed principle as L4-11 `error_taxonomy.py`:
"do not retry if you don't know").
"""
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from src.exchanges.common.error_taxonomy import ExchangeError
from src.exchanges.common.http_policy import RetryPolicy, backoff_delay
from src.exchanges.common.rate_limiter import TokenBucket
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    ProviderCandle,
    ProviderCapabilities,
    ProviderTick,
)

__all__ = ["BaseProviderAdapter", "NormalizationNotImplementedError"]

T = TypeVar("T")


class NormalizationNotImplementedError(NotImplementedError):
    """Normalization hook not implemented — instead of passing raw data through
    unnormalized, fails closed here (principle like §4.1 "no quiet zero-fill":
    do not pass through if you don't know how to normalize)."""

    def __init__(self, adapter: object, hook: str) -> None:
        super().__init__(
            f"{type(adapter).__name__}.{hook} is not implemented — returning raw data "
            "without normalization is prohibited (subclasses must override this hook)."
        )


class BaseProviderAdapter:
    """Common base inherited by `MarketDataProvider` implementations (DC-12).

    Does NOT implement Protocol methods (`capabilities`/`list_instruments`/
    `fetch_candles`/`subscribe`) — those are DC-12 vendor responsibilities.
    This class provides only `call_with_retry` (rate-limiting + retry) and
    `normalize_candle`/`normalize_tick` (normalization failure safety net)
    for internal use by its implementations.
    """

    def __init__(
        self,
        capabilities: ProviderCapabilities,
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._capabilities = capabilities
        self._provider_id = capabilities.provider_id
        self._retry_policy = retry_policy or RetryPolicy()
        self._sleep = sleep
        self._rng = rng
        self._bucket = TokenBucket(
            rate_per_sec=float(capabilities.rate_limit.requests_per_second),
            burst=float(capabilities.rate_limit.burst),
            clock=clock,
            sleep=sleep,
        )

    async def call_with_retry(
        self,
        op: Callable[[], Awaitable[T]],
        *,
        tokens: float = 1.0,
        acquire_timeout: float = 30.0,
    ) -> T:
        """Rate-limits via token bucket then executes `op()`.

        Token acquisition happens once per call (when the bucket is exhausted,
        `TokenBucket.acquire` waits and refills internally before returning — if
        waiting exceeds `acquire_timeout`, fails immediately with
        `DataProviderError(DATA_PROVIDER_RATE_LIMITED)`). Then if `op()` raises
        a retryable `DataProviderError`, retries with exponential backoff up to
        `retry_policy.max_attempts`, and propagates permanent errors, unknown
        exceptions, or cap reach-through as-is.
        """
        await self._acquire(tokens, acquire_timeout)
        attempt = 0
        while True:
            attempt += 1
            try:
                return await op()
            except DataProviderError as exc:
                if not exc.retryable or attempt >= self._retry_policy.max_attempts:
                    raise
                delay = backoff_delay(self._retry_policy, attempt, exc.retry_after_sec, self._rng)
                await self._sleep(delay)

    async def _acquire(self, tokens: float, timeout: float) -> None:
        try:
            await self._bucket.acquire(tokens, timeout=timeout)
        except ExchangeError as exc:
            raise DataProviderError(
                DataProviderErrorCode.DATA_PROVIDER_RATE_LIMITED,
                provider_id=self._provider_id,
                message=str(exc),
            ) from exc

    def normalize_candle(self, _raw: Any) -> ProviderCandle:
        """Vendor raw candle → `ProviderCandle`. Must be overridden by DC-12
        implementations — default fails immediately with
        `NormalizationNotImplementedError` instead of passing raw through."""
        raise NormalizationNotImplementedError(self, "normalize_candle")

    def normalize_tick(self, _raw: Any) -> ProviderTick:
        """Vendor raw tick → `ProviderTick`. Same fail-closed default as
        `normalize_candle`."""
        raise NormalizationNotImplementedError(self, "normalize_tick")
