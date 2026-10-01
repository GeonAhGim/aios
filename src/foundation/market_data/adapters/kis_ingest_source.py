"""LA-20 — KIS (KRX) candle source adapter (`IngestSource`, LA-9 port implementation).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-20.

Calls only `ExchangeAdapter.get_ohlcv` (`src/exchanges/common/adapter.py`)
(same contract as bitget_ingest_source/LA-15). `KISMarketDataMixin.get_ohlcv`
(`src/exchanges/kis/market_data_mixin.py`) supports only KRX daily (1d) and
minute (1m) candles and has no `start`/`end` parameters (daily candles are
fetched as the full 1900-01-01-to-today range truncated by `limit`; minute
candles always return the latest window relative to "now") — the response is
filtered client-side to the `[start, end)` range (same strategy as bitget).
How many records/days the server actually returns is **unverified** (a
KIS-side constraint symmetric to §10 R8, no measurement taken), so `limit`
stays within a conservative upper bound.

`raw_symbol` (KRX 6-digit code) is delegated to `symbol_normalizer.to_canonical`
for format validation only — for KRX the venue raw symbol and the canonical
representation are identical, so there is no conversion (LA-7).

The returned `CandleRecord.key.instrument_id` is unknown (this adapter has no
DB access, see #71 §4) — a placeholder UUID (nil) is filled in instead.
`ingest_candles` unconditionally re-keys it with the real instrument_id looked
up from reference data, so callers must not rely on this value.
"""

from __future__ import annotations

import math
from datetime import timedelta
from uuid import UUID

from pydantic import AwareDatetime

from src.data.models.market_data import Candle
from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.reference.symbol_normalizer import to_canonical
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["KisIngestSource", "UnsupportedVenueError", "UnsupportedTimeframeError"]

_MAX_LIMIT = 100  # unverified — conservative upper bound, adjust once measured
_PLACEHOLDER_INSTRUMENT_ID = UUID(int=0)
_SUPPORTED_TIMEFRAMES = frozenset({Timeframe.M1, Timeframe.D1})


class UnsupportedVenueError(ValueError):
    """`KisIngestSource` supports only `Venue.KIS_KRX`."""


class UnsupportedTimeframeError(ValueError):
    """`KISMarketDataMixin.get_ohlcv` supports only daily (1d) and minute (1m) candles."""


def _to_candle_record(candle: Candle, tf: Timeframe) -> CandleRecord:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=_PLACEHOLDER_INSTRUMENT_ID, timeframe=tf)
    return CandleRecord(
        key=key,
        open_time=candle.open_time,
        close_time=candle.close_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
    )


def _limit_for_range(start: AwareDatetime, end: AwareDatetime, tf: Timeframe, cap: int) -> int:
    span = end - start
    if span <= timedelta(0):
        return 1
    wanted = math.ceil(span / duration(tf)) + 1
    return max(1, min(wanted, cap))


class KisIngestSource:
    def __init__(self, adapter: ExchangeAdapter, *, max_limit: int = _MAX_LIMIT) -> None:
        self._adapter = adapter
        self._max_limit = min(max_limit, _MAX_LIMIT)

    async def fetch_candles(
        self,
        venue: Venue,
        raw_symbol: str,
        tf: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
    ) -> list[CandleRecord]:
        if venue is not Venue.KIS_KRX:
            raise UnsupportedVenueError(f"KisIngestSource는 KIS_KRX 전용: {venue!r}")
        if tf not in _SUPPORTED_TIMEFRAMES:
            raise UnsupportedTimeframeError(f"KIS는 1d/1m만 지원: {tf!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_candles는 tz-aware datetime만 받는다")

        canonical = to_canonical(venue, raw_symbol)
        limit = _limit_for_range(start, end, tf, self._max_limit)
        raw = await self._adapter.get_ohlcv(canonical, tf.value, limit=limit)
        return [_to_candle_record(candle, tf) for candle in raw if start <= candle.open_time < end]
