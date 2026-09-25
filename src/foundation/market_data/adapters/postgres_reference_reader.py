"""LA-24 — asyncpg implementation of `ReferenceReadRepository` (ports/reference_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

Read-only (no write path). Row-to-DTO conversion reuses `_row_to_instrument`
from the LA-12 adapter verbatim (no reimplementation). Listings use keyset
pagination in ascending order of (venue, canonical_symbol, instrument_id) —
the cursor is the last `instrument_id` of the previous page, and the sort
keys of that row are retrieved via a subquery for `>` comparison (no OFFSET;
no duplicates or gaps even if inserts occur).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.market_data.adapters.postgres_reference_repository import (
    _row_to_instrument,
)
from src.foundation.market_data.contracts.v1 import InstrumentRef, SymbolStatus, Venue
from src.foundation.market_data.ports.reference_repository import SymbolAliasRef

__all__ = ["PostgresReferenceReader"]


def _row_to_alias(row: asyncpg.Record) -> SymbolAliasRef:
    return SymbolAliasRef(
        alias_id=row["alias_id"],
        instrument_id=row["instrument_id"],
        venue=Venue(row["venue"]),
        alias_symbol=row["alias_symbol"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
    )


class PostgresReferenceReader:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_by_id(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> InstrumentRef | None:
        row = await conn.fetchrow(
            "SELECT * FROM md_instrument WHERE instrument_id = $1", instrument_id
        )
        return None if row is None else _row_to_instrument(row)

    async def list_instruments(
        self,
        conn: asyncpg.Connection,
        *,
        venues: frozenset[Venue],
        status: SymbolStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[InstrumentRef]:
        if not venues:
            return []
        rows = await conn.fetch(
            "SELECT i.* FROM md_instrument i "
            "WHERE i.venue = ANY($1::text[]) "
            "AND ($2::text IS NULL OR i.status = $2) "
            "AND ($3::uuid IS NULL OR (i.venue, i.canonical_symbol, i.instrument_id) > "
            "  (SELECT c.venue, c.canonical_symbol, c.instrument_id "
            "   FROM md_instrument c WHERE c.instrument_id = $3)) "
            "ORDER BY i.venue, i.canonical_symbol, i.instrument_id LIMIT $4",
            [v.value for v in venues],
            None if status is None else status.value,
            after,
            limit,
        )
        return [_row_to_instrument(row) for row in rows]

    async def list_aliases(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[SymbolAliasRef]:
        rows = await conn.fetch(
            "SELECT * FROM md_symbol_alias WHERE instrument_id = $1 ORDER BY valid_from ASC",
            instrument_id,
        )
        return [_row_to_alias(row) for row in rows]
