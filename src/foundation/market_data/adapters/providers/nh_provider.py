# ratchet-allow: out-of-BR-20d-2-scope SPI methods raise NotImplementedError (fail-closed stub)
"""BR-20d-2 — NH (NH Investment Securities) `MarketDataProvider` SPI delegation adapter.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Module table row 50, §9.2 DC-12 (same leaf shape as `KISProvider`, task-7293).

Same pattern as `KISProvider` (`kis_provider.py`) — delegates calls by
injecting the existing `src.exchanges.nh.adapter.NHAdapter` via constructor
without creating a new exchange client. `src/exchanges/**` is not touched in
this leaf.

Scope — `NHMarketDataMixin.get_ohlcv` only ever serves daily ("1d") candles
(any other `timeframe` argument raises `ValueError` inside the adapter
itself, see `market_data_mixin.py`), so `capabilities().timeframes` declares
only `Timeframe.D1` — avoiding violating the capability-gated principle
(§2.0-A) by not pre-declaring timeframes NH does not actually serve. Venue
scope is `Venue.NH_KRX` only (Korean domestic stocks, prerequisite task-7292,
commit 913dd982).

Unverified (do not pretend success before cross-referencing external docs):
- `history_from`: NH's `currentDaily` endpoint has no documented retention
  window. Filling with an arbitrary date would violate §4.1, so it is left
  as `None` (unknown).
- `rate_limit`: no official NH per-second limit has been confirmed yet; this
  uses the same conservative placeholder as `KISProvider` until live
  verification.
- Range-query limitation: `NHMarketDataMixin.get_ohlcv` does not accept
  `[start, end)` range parameters (only "latest `limit` rows"). This
  provider accepts the result and post-filters by `span` — past ranges the
  adapter cannot reach at all become an honest `DATA_COVERAGE_MISSING`
  (§4.1, not zero-fill).

`list_instruments`/`subscribe` are not implementation targets for this leaf
(same rationale as `KISProvider` — instrument_id (ULID) issuance is DC-2
responsibility, realtime stream wiring is DC-17 responsibility). Both raise
`NotImplementedError` (fail-closed).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from decimal import Decimal

from src.data.models.base import AssetClass
from src.exchanges.common.http_policy import RetryPolicy
from src.exchanges.nh.adapter import NHAdapter
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

__all__ = ["NHProvider"]

_MAX_CANDLES_PER_REQUEST = 100  # Same default as NHMarketDataMixin.get_ohlcv.
_SUPPORTED_TIMEFRAMES = frozenset({Timeframe.D1})

_CAPABILITIES = ProviderCapabilities(
    provider_id="nh",
    asset_classes=frozenset({AssetClass.KR_EQUITY}),
    timeframes=_SUPPORTED_TIMEFRAMES,
    history_from=None,  # Unverified (no doc cross-reference) — do not fill with arbitrary date.
    realtime=True,  # NHAdapter.get_capabilities().supports_websocket
    delayed_seconds=0,
    max_symbols_per_request=1,  # REST endpoint queries one symbol at a time.
    rate_limit=RateLimitSpec(requests_per_second=Decimal(15), burst=15),  # Unverified
)


class NHProvider(BaseProviderAdapter):
    """`MarketDataProvider` (DC-5) implementation that delegates to
    `NHAdapter` (existing `src/exchanges/nh`). Domestic stocks only
    (`Venue.NH_KRX`), daily candles only (see module docstring)."""

    def __init__(
        self,
        adapter: NHAdapter,
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
            "NHProvider.list_instruments: BR-20d-2 스콥 밖 — instrument_id(ULID) "
            "발급은 DC-2 심볼 마스터 소관이며 이 SPI 계층은 그 저장소를 참조하지 "
            "않는다(task-7293 decision, KISProvider와 동일)."
        )

    async def fetch_candles(
        self, listing: VenueListing, tf: Timeframe, span: TimeSpan
    ) -> CandleColumns:
        if listing.venue is not Venue.NH_KRX:
            raise ValueError(f"NHProvider는 Venue.NH_KRX listing만 처리한다: {listing.venue!r}")
        if tf not in _SUPPORTED_TIMEFRAMES:
            raise ValueError(
                f"NHProvider는 {sorted(t.value for t in _SUPPORTED_TIMEFRAMES)}만 "
                f"지원한다: {tf.value!r}"
            )
        symbol = to_canonical(Venue.NH_KRX, listing.venue_symbol)

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
                        f"nh: {symbol} {tf.value} 구간 [{span.start}, {span.end}) 데이터 없음"
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
            "NHProvider.subscribe: BR-20d-2 스콥 밖 — 실시간 스트림 배선은 "
            "DC-17(realtime_fanout) 선행 리프 몫이다(task-7293 decision, "
            "KISProvider와 동일)."
        )
        yield  # pragma: no cover — unreachable marker so mypy infers the AsyncIterator return type
