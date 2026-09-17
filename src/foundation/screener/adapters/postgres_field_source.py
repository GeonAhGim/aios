"""asyncpg `ScreenerFieldSource` — U-1b execution engine storage.

Universe indexing delegates to `PostgresReferenceReader.list_instruments`
(LA-24) instead of adding a second `md_instrument` query path (DC-13
decision's "don't reimplement if it already overlaps" applies here too).

Field values reuse DC-13's `md_candle` table directly with a batched
"latest bar per instrument, per venue" query (`DISTINCT ON`) — this is a
capability `HotPostgresStorage`/`PostgresCandleStore` don't offer (both are
single-`instrument_id`-keyed), so it is not a duplicate of their SQL; it is
the "index" this leaf (UX-6) is responsible for adding. Grouping by venue
(not one query per instrument) is what keeps the 1,000-row/5,000-symbol
scan budgets (§9, ADR-2026-09-09-C) reachable.

Timeframe is fixed to daily (`Timeframe.D1`) — "current value" for a
screener row means the latest completed daily bar; intraday screening is a
follow-up leaf's decision, not this one's."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.foundation.market_data.adapters.postgres_reference_reader import PostgresReferenceReader
from src.foundation.market_data.contracts.v1 import InstrumentRef, SymbolStatus, Timeframe, Venue

__all__ = ["PostgresScreenerFieldSource"]

_FIELD_COLUMNS = ("open", "high", "low", "close", "volume")


class PostgresScreenerFieldSource:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._reader = PostgresReferenceReader(pool)

    async def universe_page(
        self, *, venues: frozenset[Venue], after: UUID | None, limit: int
    ) -> list[InstrumentRef]:
        if not venues:
            return []
        async with self._pool.acquire() as conn:
            return await self._reader.list_instruments(
                conn,
                venues=venues,
                status=SymbolStatus.LISTED,
                after=after,
                limit=limit,
            )

    async def read_fields(
        self,
        *,
        instrument_ids_by_venue: Mapping[Venue, Sequence[UUID]],
        field_names: frozenset[str],
        as_of: datetime,
    ) -> dict[UUID, dict[str, Decimal]]:
        columns = [c for c in _FIELD_COLUMNS if c in field_names]
        if not columns:
            return {}
        select_cols = ", ".join(columns)
        result: dict[UUID, dict[str, Decimal]] = {}
        async with self._pool.acquire() as conn:
            for venue, instrument_ids in instrument_ids_by_venue.items():
                if not instrument_ids:
                    continue
                rows = await conn.fetch(
                    # select_cols is filtered against the _FIELD_COLUMNS module
                    # constant above, never user input.
                    f"SELECT DISTINCT ON (instrument_id) instrument_id, {select_cols} "  # noqa: S608
                    "FROM md_candle "
                    "WHERE venue = $1 AND timeframe = $2 "
                    "AND instrument_id = ANY($3::uuid[]) AND open_time <= $4 "
                    "ORDER BY instrument_id, open_time DESC",
                    venue.value,
                    Timeframe.D1.value,
                    list(instrument_ids),
                    as_of,
                )
                for row in rows:
                    result[row["instrument_id"]] = {c: row[c] for c in columns}
        return result
