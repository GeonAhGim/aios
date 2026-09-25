"""LB-14 — Candle-based mark price source (adapters/candle_mark_price_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14.

Uses the latest 1-minute candle close price from A(`market_data`) as the mark
price. Reuses LA-5 `stale_detector.detect_stale` (k×duration threshold, k=3
default) from task-654 decision rather than building a new stale check — returns
`None` (not `0` or the previous value) when there is no mark or the candle is
stale (per the port contract in `ports/mark_price_source.py` docstring).

This leaf does not guarantee that `PositionKey.instrument_id`
(`venue:instrument_id:strategy_id:execution_id`, the second field) actually
matches any alias in the market_data reference data
(`md_symbol_alias.alias_symbol`) — if
`ReferenceRepository.get_instrument` returns `None` (not registered or format
mismatch), the mark is simply `None`, following the same approximate-treatment
spirit described in the §9 LA-17 `get_candles.py` module docstring.

If `venue` is not a `market_data.Venue` (BITGET/KIS_KRX/KIS_US) — e.g. a
paper/backtest-only venue string — the lookup is never attempted and `None` is
returned immediately; this means no candle source exists for that venue yet,
not an error.
"""
from __future__ import annotations

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, Money
from src.foundation.market_data.api import KNOWN_SESSIONS, detect_stale, duration
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import ReferenceRepository
from src.foundation.positions.domain.position_key import PositionKey

__all__ = ["CandleMarkPriceSource"]

_STALE_K = 3  # Same default as LA-5 stale_detector (task-654 decision).


class CandleMarkPriceSource:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        store: CandleStore,
        refs: ReferenceRepository,
        cal: CalendarRepository,
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self._pool = pool
        self._store = store
        self._refs = refs
        self._cal = cal
        self._timeframe = timeframe

    async def mark(self, position_key: str, at: AwareDatetime) -> Money | None:
        key = PositionKey.parse(position_key)
        try:
            venue = Venue(key.venue.upper())
        except ValueError:
            return None

        async with self._pool.acquire() as conn:
            instrument = await self._refs.get_instrument(conn, venue, key.instrument_id, at)
            if instrument is None or instrument.quote is None:
                return None
            try:
                currency = Currency(instrument.quote)
            except ValueError:
                return None

            series_key = SeriesKey(
                venue=venue, instrument_id=instrument.instrument_id, timeframe=self._timeframe
            )
            step = duration(self._timeframe)
            last_open = await self._store.last_open_time(conn, series_key)
            if last_open is None:
                return None
            candles = await self._store.query(
                conn, series_key, last_open, last_open + step, None
            )
            if not candles:
                return None
            candle = candles[-1]

            session_open = await self._is_session_open(conn, venue, at)

        if detect_stale(candle.open_time, at, self._timeframe, session_open, k=_STALE_K):
            return None
        return Money(amount=candle.close, currency=currency)

    async def _is_session_open(
        self, conn: asyncpg.Connection, venue: Venue, at: AwareDatetime
    ) -> bool:
        spec = KNOWN_SESSIONS[venue.value]
        if spec.continuous:
            return True
        calendar = await self._cal.load(conn, venue, at.astimezone(spec.tz).year)
        return calendar.is_open(at)
