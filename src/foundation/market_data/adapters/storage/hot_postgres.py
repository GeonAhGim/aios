"""DC-13 — hot layer (recent partition) candle storage: read/write by `instrument_id` key.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-13, §9.2 DC-13 (DoD: `instrument_id` key lookup p95 200ms, 5,000 bars).

The decision for this leaf (task-1212) prohibits creating new migrations and explicitly
requires reusing LA-11 `md_candle` (migration `4a1d0c0de008`, task-450 commit `c7ff06f`)
and its partition creation function `md_ensure_partitions` as-is — "hot layer" is not a
physically different table from `md_candle` but a logical name referring to the most
recent partition among them (promotion and archival of old partitions to warm-layer
Parquet is the responsibility of DC-15 `tiering.py`, out of scope for this leaf).

`query`/`upsert_batch`/`read_candles_columnar` are already implemented by
LA-13 `adapters/postgres_candle_store.PostgresCandleStore` on top of `md_candle`
(operating under the same CHECK/PK/WORM constraints); if this adapter rewrites that
SQL, it would itself violate the DC-13 decision's prohibition on "reimplementing when
functions overlap" — therefore `HotPostgresStorage` is a thin facade that delegates to
`PostgresCandleStore`. What this leaf actually adds is (1) a surface that lets callers
invoke without constructing `SeriesKey` directly, using only `instrument_id`, `venue`,
and `timeframe`, and (2) a wrapper around `md_ensure_partitions` calls (pre-creating
partitions).

Boundary (honestly documenting deviations from the spec): the `instrument_id` accepted
here is the identifier of `md_instrument` (LA namespace, UUID) referenced by
`md_candle.instrument_id`. DC-8 `coverage_spans`/`instruments` (DC-4, task-1195) uses
a separate `VARCHAR(26)` ULID `instrument_id` — mapping between these two identifier
spaces is out of scope for this leaf (which leaf creates that bridge is a
needs_decision item; it will need to be decided when DC-16 backfill jobs or DC-9
entitlement checks must handle both values simultaneously)."""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.adapters.postgres_candle_store import PostgresCandleStore
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = ["HotPostgresStorage"]


class HotPostgresStorage:
    """Read/write `md_candle` (hot layer) — thin `PostgresCandleStore` delegation facade.

    `conn` is an `asyncpg.Connection` already opened by the caller (same contract as
    LA-9 `ports/candle_store.CandleStore` — transaction boundaries are the caller's
    responsibility, not this adapter's)."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._candle_store = PostgresCandleStore(pool)

    async def read_columns(
        self,
        conn: asyncpg.Connection,
        instrument_id: UUID,
        venue: Venue,
        timeframe: Timeframe,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None = None,
    ) -> CandleColumns:
        """Return column arrays for the `[start, end)` range, sorted by `open_time ASC`
        (reused without redefining `CandleColumns` per ADR-2026-09-04-A #1).
        Non-existent `instrument_id`, empty ranges, and future ranges yield an empty
        `CandleColumns` because the WHERE clause returns 0 rows — this method does not
        raise exceptions. (Separate from the invariant in §6 that forbids filling
        out-of-coverage ranges with 0/NaN: this method does not make "coverage
        determinations"; it reports raw storage state as-is. Coverage-missing
        determinations and `DATA_COVERAGE_MISSING` errors are the responsibility of
        `domain/coverage/gaps.py` (DC-7).)"""
        key = SeriesKey(venue=venue, instrument_id=instrument_id, timeframe=timeframe)
        return await self._candle_store.read_candles_columnar(conn, key, start, end, as_of)

    async def write_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        """§5 `ON CONFLICT (venue, instrument_id, timeframe, open_time) DO
        NOTHING` (as in LA-13) — idempotent re-collection; return value is the count
        of rows actually inserted."""
        return await self._candle_store.upsert_batch(conn, batch_id, candles)

    async def ensure_partitions(self, conn: asyncpg.Connection, months_ahead: int = 3) -> None:
        """Call `md_ensure_partitions(months_ahead)` (migration `4a1d0c0de008`,
        SECURITY DEFINER) — do not create new partition DDL here (per decision).
        This function creates partitions only forward from the current month (per
        the migration docstring) — backfilling past ranges requires the target
        partition to already exist."""
        await conn.execute("SELECT md_ensure_partitions($1)", months_ahead)
