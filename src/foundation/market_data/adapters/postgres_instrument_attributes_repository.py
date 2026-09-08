"""DC-21 -- asyncpg adapter for the append-only `instrument_attributes` table.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
Section 9.10 DC-21.

`record()` only ever INSERTs (migration `b2927f5a25c2`'s WORM trigger
rejects UPDATE/DELETE outright, including for the table owner). `get_as_of()`
pre-filters by `known_at <= as_of` in SQL for efficiency, then hands the raw
rows to `domain.point_in_time.latest_attributes_as_of` -- the "one row per
attr_key, latest known_at wins" reduction is reference-data policy, not a
SQL concern, and it delegates its instant comparison to FA-9's
`core.bitemporal.as_of` rather than reimplementing it here.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.domain.point_in_time import (
    ReferenceAttribute,
    latest_attributes_as_of,
)

__all__ = ["PostgresInstrumentAttributesRepository"]


def _row_to_attribute(row: asyncpg.Record) -> ReferenceAttribute:
    return ReferenceAttribute(
        instrument_id=row["instrument_id"],
        attr_key=row["attr_key"],
        attr_value=row["attr_value"],
        known_at=row["known_at"],
        recorded_by=None if row["recorded_by"] is None else str(row["recorded_by"]),
    )


class PostgresInstrumentAttributesRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def record(
        self,
        conn: asyncpg.Connection,
        *,
        instrument_id: str,
        attr_key: str,
        attr_value: str,
        known_at: AwareDatetime,
        recorded_by: UUID | None = None,
    ) -> None:
        """Append a new correction row -- there is no update path by design."""
        await conn.execute(
            "INSERT INTO instrument_attributes "
            "(instrument_id, attr_key, attr_value, known_at, recorded_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            instrument_id,
            attr_key,
            attr_value,
            known_at,
            recorded_by,
        )

    async def get_as_of(
        self, conn: asyncpg.Connection, instrument_id: str, *, as_of: AwareDatetime
    ) -> dict[str, ReferenceAttribute]:
        """Latest known value per `attr_key` visible at `as_of` (§9.10 DC-21 DoD)."""
        rows = await conn.fetch(
            "SELECT * FROM instrument_attributes "
            "WHERE instrument_id = $1 AND known_at <= $2",
            instrument_id,
            as_of,
        )
        attributes = [_row_to_attribute(row) for row in rows]
        return latest_attributes_as_of(attributes, as_of=as_of)
