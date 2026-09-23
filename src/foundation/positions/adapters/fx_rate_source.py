"""LB-14 — Candle-based FX rate source (adapters/fx_rate_source.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-14, R7.

**Unverified (R7)**: This leaf does not define which venue/instrument combination
the "median price from Bitget/KIS references" actually refers to — institutional
benchmarks such as the Seoul Foreign Exchange Market reference rate have not yet
been adopted (R7 "unconfirmed"), and if this leaf codifies that judgment it would
assert unverified facts as code. Instead, the caller injects via the `references`
constructor argument a list of `SeriesKey` to use as references per currency pair
(each series' latest 1-minute bar close is assumed to mean "1 base = X quote" —
supplying a series with the wrong direction yields the inverse rate; wiring
responsibility lies with the caller).

Stale detection reuses the same LA-5 `detect_stale`(k=3) as
`candle_mark_price_source.py` (task-654 decision) — if an individual reference
series is stale/missing, its value is excluded and the median is computed from
the remaining series. If all are absent, the result is `None` (not replaced with
`0`) — see the port contract `ports/fx_rate_source.py` docstring."""
from __future__ import annotations

from decimal import Decimal
from statistics import median

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, FXRate
from src.foundation.market_data.api import KNOWN_SESSIONS, detect_stale, duration
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe
from src.foundation.market_data.ports.calendar_repository import CalendarRepository
from src.foundation.market_data.ports.candle_store import CandleStore

__all__ = ["CandleFxRateSource"]

_STALE_K = 3  # Same as LA-5 stale_detector default (task-654 decision).


class CandleFxRateSource:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        store: CandleStore,
        cal: CalendarRepository,
        references: dict[tuple[Currency, Currency], list[SeriesKey]],
        timeframe: Timeframe = Timeframe.M1,
    ) -> None:
        self._pool = pool
        self._store = store
        self._cal = cal
        self._references = references
        self._timeframe = timeframe

    async def rate(self, base: Currency, quote: Currency, at: AwareDatetime) -> FXRate | None:
        keys = self._references.get((base, quote))
        invert = False
        if keys is None:
            keys = self._references.get((quote, base))
            invert = True
        if not keys:
            return None

        async with self._pool.acquire() as conn:
            legs = [leg for leg in [await self._latest_leg(conn, k, at) for k in keys] if leg]
        if not legs:
            return None

        value = median(price for price, _ in legs)
        oldest = min(ts for _, ts in legs)
        if invert:
            if value == 0:
                return None
            value = Decimal(1) / value
        return FXRate(base=base, quote=quote, rate=value, timestamp=oldest, source="candle_median")

    async def _latest_leg(
        self, conn: asyncpg.Connection, key: SeriesKey, at: AwareDatetime
    ) -> tuple[Decimal, AwareDatetime] | None:
        step = duration(self._timeframe)
        last_open = await self._store.last_open_time(conn, key)
        if last_open is None:
            return None
        candles = await self._store.query(conn, key, last_open, last_open + step, None)
        if not candles:
            return None
        candle = candles[-1]

        spec = KNOWN_SESSIONS[key.venue.value]
        session_open = (
            True
            if spec.continuous
            else (await self._cal.load(conn, key.venue, at.astimezone(spec.tz).year)).is_open(at)
        )
        if detect_stale(candle.open_time, at, self._timeframe, session_open, k=_STALE_K):
            return None
        return candle.close, candle.open_time
