# ratchet-allow: out-of-DC-12-scope SPI methods raise NotImplementedError (fail-closed stub)
"""DC-12 — Bitget `MarketDataProvider` SPI delegation adapter.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2 module table row 50, §9.2 DC-12 (prerequisite DC-11, task-1187 b0b8bed merged).

This file does not create a new exchange client — it receives an existing
`src.exchanges.bitget.BitgetAdapter` (REST auth/signature/retry is its
responsibility) via constructor and delegates only to
`MarketDataProvider` Protocol calls (DC-5 `ports/provider.py`, 554f078).
`src/exchanges/**` is not touched by this leaf (task-1211 decision).

Values declared in `capabilities()` do not conflict with
`BitgetAdapter.get_capabilities()` (`ExchangeCapability`, Phase 1
capability-gated declaration). Trading and data capability declarations are
independent axes, but this adapter does not declare values beyond the
range it can actually call (timeframes supported by
`BitgetMarketDataMixin.get_ohlcv`/`get_history_candles`).

Unverified (do not fabricate success prior to external doc comparison):
- `history_from`: The actual retention start point of Bitget candle API
  history has not been confirmed against official docs. Filling with an
  arbitrary date would violate the spirit of §4.1 "no silent fill", so it
  is left as `None` (unknown).
- `rate_limit`: The per-request-second limit for Bitget v2 spot public
  endpoints uses a conservative estimate (10 req/s, burst 20) until live
  verification.
- `_MAX_CANDLES_PER_REQUEST`: Documented upper limit unconfirmed;
  conservatively capped at 200.

`list_instruments`/`subscribe` are not implementation targets of this leaf
(task-1211 decision — "the Protocol to implement is ... capabilities()...
fetch_candles()..."). `VenueListing.instrument_id` returned by
`list_instruments` is a ULID issued by the DC-2 symbol master, and this SPI
layer does not access that store (DC-5
`ports/instrument_repository.py`), so fabricating one here risks violating
the §4.1 invariant (`instrument_id` immutable & unique).
`subscribe` (real-time stream wiring) is the prerogative of the DC-17
(`realtime_fanout`) prerequisite leaf. Both raise `NotImplementedError` for
fail-closed behavior — returning a silent empty result would make "not
supported" and "not yet done" indistinguishable.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from decimal import Decimal

from src.data.models.base import AssetClass
from src.exchanges.bitget.adapter import BitgetAdapter
from src.exchanges.common.http_policy import RetryPolicy
from src.foundation.market_data.adapters.providers.base_adapter import BaseProviderAdapter
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.contracts.v2.instruments import VenueListing
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.ports.provider import (
    DataProviderError,
    DataProviderErrorCode,
    ProviderCapabilities,
    RateLimitSpec,
    TickOrCandle,
    TimeSpan,
)

__all__ = ["BitgetProvider"]

_MAX_CANDLES_PER_REQUEST = 200  # Unverified (no doc cross-check); pagination out of scope.

_CAPABILITIES = ProviderCapabilities(
    provider_id="bitget",
    asset_classes=frozenset({AssetClass.CRYPTO}),
    timeframes=frozenset(
        {
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.M30,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        }
    ),
    history_from=None,  # Unverified (no doc cross-check) — do not fill with an arbitrary date.
    realtime=True,  # BitgetAdapter.get_capabilities().supports_websocket
    delayed_seconds=0,
    max_symbols_per_request=1,  # REST candles endpoint accepts only one symbol at a time.
    rate_limit=RateLimitSpec(requests_per_second=Decimal(10), burst=20),  # Unverified
)


class BitgetProvider(BaseProviderAdapter):
    """`MarketDataProvider`(DC-5) implementation delegating to
    `BitgetAdapter`(existing `src/exchanges/bitget`)."""

    def __init__(
        self,
        adapter: BitgetAdapter,
        *,
        retry_policy: RetryPolicy | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Callable[[], float] = random.random,
    ) -> None:
        super().__init__(
            _CAPABILITIES, retry_policy=retry_policy, clock=clock, sleep=sleep, rng=rng
        )
        self._adapter = adapter

    def capabilities(self) -> ProviderCapabilities:
        return _CAPABILITIES

    async def list_instruments(self, asset_class: AssetClass) -> list[VenueListing]:
        raise NotImplementedError(
            "BitgetProvider.list_instruments: out of DC-12 scope — instrument_id(ULID) "
            "issuance is DC-2 symbol master responsibility"
        )

    async def fetch_candles(
        self,
        listing: VenueListing,
        tf: Timeframe,
        span: TimeSpan,
    ) -> CandleColumns:
        if listing.venue is not Venue.BITGET:
            raise ValueError(
                f"BitgetProvider handles only Venue.BITGET listings: {listing.venue!r}"
            )
        symbol = to_canonical(Venue.BITGET, listing.venue_symbol)

        async def _op() -> CandleColumns:
            end_ms = str(int(span.end.timestamp() * 1000))
            raw_candles = await self._adapter.get_history_candles(
                symbol, tf.value, limit=_MAX_CANDLES_PER_REQUEST, end_time=end_ms
            )
            in_span = sorted(
                (c for c in raw_candles if span.start <= c.open_time < span.end),
                key=lambda c: c.open_time,
            )
            if not in_span:
                raise DataProviderError(
                    DataProviderErrorCode.DATA_COVERAGE_MISSING,
                    provider_id=self._provider_id,
                    message=(f"bitget: {symbol} {tf.value} no data in [{span.start}, {span.end})"),
                )
            return CandleColumns(
                ts=[c.open_time for c in in_span],
                open=[c.open for c in in_span],
                high=[c.high for c in in_span],
                low=[c.low for c in in_span],
                close=[c.close for c in in_span],
                volume=[c.volume for c in in_span],
                quote_volume=[None for _ in in_span],
            )

        return await self.call_with_retry(_op)

    async def subscribe(self, _listings: Sequence[VenueListing]) -> AsyncIterator[TickOrCandle]:
        raise NotImplementedError(
            "BitgetProvider.subscribe: out of DC-12 scope — real-time stream wiring "
            "is DC-17(realtime_fanout) prerequisite responsibility(task-1211 decision)."
        )
        yield  # pragma: no cover — unreachable marker for mypy AsyncIterator type inference
