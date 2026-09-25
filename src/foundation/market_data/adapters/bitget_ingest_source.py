"""LA-15 — Bitget candle source adapter (`IngestSource`, LA-9 port impl).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-15, §10 R8.

Calls only `ExchangeAdapter.get_ohlcv` (`src/exchanges/common/adapter.py`) —
the Bitget-specific pagination method (`get_history_candles`) is not on this
abstract interface and must not be used (module table §2.2 LA-15 dependency =
`adapter.py` only). `get_ohlcv` has no `start`/`end` params (returns only the
most recent `limit` candles), so the response is client-side filtered to the
`[start, end)` window — if the requested range is older than what the exchange
actually returns as the latest window the result may be an empty list
(**unverified**: server pagination / time offset, §10 R8).

Compute `limit` from the number of candles needed for the requested span (+1
headroom) but never exceed the §10 R8 "conservatively cap at 200" upper bound
(this is a value introduced without live measurement, so we keep it conservative).

Accepts `raw_symbol` (port param, venue-native symbol, e.g. "BTCUSDT") and
converts it internally via `symbol_normalizer.to_canonical` to "BTC/USDT"
format before passing to `get_ohlcv` (symmetric with
`BitgetMarketDataMixin.get_ohlcv` which runs `to_bitget_symbol` again — do not
re-implement LA-7 rules, just delegate).

The returned `CandleRecord.key.instrument_id` is unknown (this adapter knows
no DB, #71 §4) — fill with a placeholder UUID (nil). `ingest_candles` will
always re-key with the real `instrument_id` looked up from reference data, so
callers must not depend on this value.
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

__all__ = ["BitgetIngestSource", "UnsupportedVenueError"]

_MAX_LIMIT = 200  # §10 R8 unverified — conservative cap, adjust after live measurement
_PLACEHOLDER_INSTRUMENT_ID = UUID(int=0)


class UnsupportedVenueError(ValueError):
    """`BitgetIngestSource` supports only `Venue.BITGET`."""


def _to_candle_record(candle: Candle, tf: Timeframe) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=_PLACEHOLDER_INSTRUMENT_ID, timeframe=tf)
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


class BitgetIngestSource:
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
        if venue is not Venue.BITGET:
            raise UnsupportedVenueError(f"BitgetIngestSource is BITGET-only: {venue!r}")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("fetch_candles accepts tz-aware datetime only")

        canonical = to_canonical(venue, raw_symbol)
        limit = _limit_for_range(start, end, tf, self._max_limit)
        raw = await self._adapter.get_ohlcv(canonical, tf.value, limit=limit)
        return [
            _to_candle_record(candle, tf) for candle in raw if start <= candle.open_time < end
        ]
