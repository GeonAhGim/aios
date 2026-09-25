# ratchet-allow: out-of-DC-12-scope SPI methods raise NotImplementedError (fail-closed stub)
"""DC-12 — KIS (Korea Investment & Securities) `MarketDataProvider` SPI delegation adapter.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Module table row 50, §9.2 DC-12 (prerequisite DC-11, task-1187 b0b8bed merged).

Same pattern as `BitgetProvider` (`bitget_provider.py`) — delegates calls by
injecting the existing `src.exchanges.kis.KISAdapter` via constructor without
creating a new exchange client. `src/exchanges/**` is not touched in this
leaf (task-1211 decision).

Scope — As `KISAdapter.get_capabilities()` declares only `KR_EQUITY` under
the Phase 1 capability-gated principle (overseas stocks/futures & options are
Draft), this SPI layer handles only `Venue.KIS_KRX` (Korean domestic stocks).
`KISMarketDataMixin.get_ohlcv` actually supports only two timeframes: daily
(`1d`) and 1-minute (`1m`) (02d spec §2; the exchange does not provide other
minute intervals), so `capabilities().timeframes` declares only those two —
avoiding violating the capability-gated principle (§2.0-A) by not pre-declaring
timeframes that appear unsupported.

Unverified (do not pretend success before cross-referencing external docs):
- `history_from`: KIS daily queries send `FID_INPUT_DATE_1="19000101"`, but
  it has not been confirmed whether the server actually retains that much
  historical data. Filling with an arbitrary date would violate §4.1, so it
  is left as `None` (unknown).
- `rate_limit`: The KIS official docs' per-second limit uses a conservative
  estimate (15 req/s, burst 15) until live verification.
- Range-query limitation: `KISMarketDataMixin.get_ohlcv` does not accept
  `[start, end)` range parameters (the existing adapter supports only
  "latest `limit` rows"), and for daily candles it queries from 1900 to
  today but trims the response to `limit`. This provider therefore accepts
  the result and post-filters by `span` — past ranges the adapter cannot
  reach at all become a honest `DATA_COVERAGE_MISSING` (§4.1, not zero-fill).

`list_instruments`/`subscribe` are not implementation targets for this leaf
(task-1211 decision, same rationale as `BitgetProvider` — instrument_id
(ULID) issuance is DC-2 responsibility, realtime stream wiring is DC-17
responsibility). Both raise `NotImplementedError` (fail-closed).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from decimal import Decimal

from src.data.models.base import AssetClass
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.kis.adapter import KISAdapter
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

__all__ = ["KISProvider"]

_MAX_CANDLES_PER_REQUEST = 100  # Same as KISMarketDataMixin.get_ohlcv default.
_SUPPORTED_TIMEFRAMES = frozenset({Timeframe.M1, Timeframe.D1})

_CAPABILITIES = ProviderCapabilities(
    provider_id="kis",
    asset_classes=frozenset({AssetClass.KR_EQUITY}),
    timeframes=_SUPPORTED_TIMEFRAMES,
    history_from=None,  # Unverified (no doc cross-reference) — do not fill with arbitrary date.
    realtime=True,  # KISAdapter.get_capabilities().supports_websocket
    delayed_seconds=0,
    max_symbols_per_request=1,  # REST endpoint queries one symbol at a time.
    rate_limit=RateLimitSpec(requests_per_second=Decimal(15), burst=15),  # Unverified
)


class KISProvider(BaseProviderAdapter):
    """`MarketDataProvider` (DC-5) implementation that delegates to
    `KISAdapter` (existing `src/exchanges/kis`). Domestic stocks only
    (`Venue.KIS_KRX`) (Phase 1 capability-gated scope, see module docstring)."""

    def __init__(
        self,
        adapter: KISAdapter,
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
            "KISProvider.list_instruments: DC-12 스콥 밖 — instrument_id(ULID) "
            "발급은 DC-2 심볼 마스터 소관이며 이 SPI 계층은 그 저장소를 참조하지 "
            "않는다(task-1211 decision)."
        )

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        if listing.venue is not Venue.KIS_KRX:
            raise ValueError(f"KISProvider는 Venue.KIS_KRX listing만 처리한다: {listing.venue!r}")
        if tf not in _SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"KISProvider는 {sorted(t.value for t in _SUPPORTED_TIMEFRAMES)}만 "
                f"지원한다: {tf.value!r}"
            )
        symbol = to_canonical(Venue.KIS_KRX, listing.venue_symbol)

        async def _op() -> CandleColumns:
            raw_candles = await self._adapter.get_ohlcv(
                symbol, tf.value, limit=_MAX_CANDLES_PER_REQUEST
            )
            in_span = sorted(
                (c for c in raw_candles if span.start <= c.open_time < span.end),
                key=lambda c: c.open_time,
            )
            if not in_span:
                raise DataProviderError(
                    DataProviderErrorCode.DATA_COVERAGE_MISSING,
                    provider_id=self._provider_id,
                    message=(
                        f"kis: {symbol} {tf.value} 구간 [{span.start}, {span.end}) 데이터 없음"
                    ),
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
            "KISProvider.subscribe: DC-12 스콥 밖 — 실시간 스트림 배선은 "
            "DC-17(realtime_fanout) 선행 리프 몫이다(task-1211 decision)."
        )
        yield  # pragma: no cover — mypy가 AsyncIterator 반환형을 추론하도록 하는 도달 불가 표식
