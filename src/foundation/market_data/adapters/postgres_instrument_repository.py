"""DC-8 — asyncpg implementation of `ports/instrument_repository.py`(DC-5).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5·DC-8, §3.2(contracts), §4.1(invariants), §9.2 DC-8.

DC-4(dbaf260f2917) migration created `instruments`/`venue_listings`
tables; this module implements the `InstrumentRepository` Protocol on top
of them — field/method signatures follow the port definition verbatim
(task-1195 decision).

`instrument_id` immutability and `venue_listings` period overlap prohibition
(§4.1) are already enforced by DB constraints (trigger + `EXCLUDE USING gist`,
DC-4), so this adapter does not re-check at the app level; it only translates
the resulting exceptions (`CheckViolationError`/`ExclusionViolationError`) into
domain exceptions.

`update_lifecycle_state` follows the "read-state-as-write-condition" rule
(standard-105 concurrency rule #1): DC-3 (`domain/instruments/lifecycle.py`)
reads the current state, computes the next via `transition()`, then calls this
method. Because another transaction may have already changed the state in the
gap, ignoring a mismatch without an `expected_state` WHERE clause is not
acceptable (e.g. if ACTIVE→HALT and ACTIVE→DELIST race, the later commit wins
silently). Mismatches are raised as the shared
`ConcurrencyConflictError` (`src/core/db/conditional_write.py`); when the
instrument_id itself is missing, `InstrumentNotFoundError` is raised instead
(same pattern as `postgres_balance_repository.apply` — on failure, verify
existence with an extra SELECT).
"""
from __future__ import annotations

import asyncpg
from pydantic import AwareDatetime

from src.core.db.conditional_write import ConcurrencyConflictError
from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)

__all__ = [
    "DuplicateInstrumentIdError",
    "InstrumentNotFoundError",
    "VenueListingOverlapError",
    "PostgresInstrumentRepository",
]


class DuplicateInstrumentIdError(Exception):
    """`create()` called with an already-existing `instrument_id` — §4.1
    `instrument_id` is immutable; there is no UPDATE path through this
    method (wraps instruments PK violation as a domain exception)."""


class InstrumentNotFoundError(Exception):
    """`update_lifecycle_state()` called with a non-existent `instrument_id` —
    raises fail-closed instead of silently ignoring."""


class VenueListingOverlapError(Exception):
    """`add_listing()` claims an overlapping period for the same
    (venue, venue_symbol) — violates the `EXCLUDE USING gist` constraint
    on `venue_listings` (DC-4)."""


def _row_to_instrument(row: asyncpg.Record) -> Instrument:
    return Instrument(
        instrument_id=row["instrument_id"],
        asset_class=AssetClass(row["asset_class"]),
        base=row["base"],
        quote=row["quote"],
        isin=row["isin"],
        figi=row["figi"],
        tick_size=row["tick_size"],
        lot_size=row["lot_size"],
        calendar_id=row["calendar_id"],
        lifecycle_state=InstrumentLifecycle(row["lifecycle_state"]),
        created_at=row["created_at"],
    )


def _row_to_listing(row: asyncpg.Record) -> VenueListing:
    return VenueListing(
        instrument_id=row["instrument_id"],
        venue=Venue(row["venue"]),
        venue_symbol=row["venue_symbol"],
        listed_at=row["listed_at"],
        delisted_at=row["delisted_at"],
        is_primary=row["is_primary"],
    )


class PostgresInstrumentRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, conn: asyncpg.Connection, instrument_id: str) -> Instrument | None:
        row = await conn.fetchrow(
            "SELECT * FROM instruments WHERE instrument_id = $1", instrument_id
        )
        return None if row is None else _row_to_instrument(row)

    async def create(self, conn: asyncpg.Connection, instrument: Instrument) -> Instrument:
        try:
            row = await conn.fetchrow(
                "INSERT INTO instruments "
                "(instrument_id, asset_class, base, quote, isin, figi, tick_size, "
                " lot_size, calendar_id, lifecycle_state, created_at) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) RETURNING *",
                instrument.instrument_id,
                instrument.asset_class.value,
                instrument.base,
                instrument.quote,
                instrument.isin,
                instrument.figi,
                instrument.tick_size,
                instrument.lot_size,
                instrument.calendar_id,
                instrument.lifecycle_state.value,
                instrument.created_at,
            )
        except asyncpg.exceptions.UniqueViolationError as exc:
            raise DuplicateInstrumentIdError(
                f"이미 등록된 instrument_id: {instrument.instrument_id}"
            ) from exc
        return _row_to_instrument(row)

    async def update_lifecycle_state(
        self,
        conn: asyncpg.Connection,
        instrument_id: str,
        *,
        expected_state: InstrumentLifecycle,
        state: InstrumentLifecycle,
    ) -> Instrument:
        row = await conn.fetchrow(
            "UPDATE instruments SET lifecycle_state = $1 "
            "WHERE instrument_id = $2 AND lifecycle_state = $3 RETURNING *",
            state.value,
            instrument_id,
            expected_state.value,
        )
        if row is not None:
            return _row_to_instrument(row)
        exists = await conn.fetchval(
            "SELECT 1 FROM instruments WHERE instrument_id = $1", instrument_id
        )
        if not exists:
            raise InstrumentNotFoundError(f"instrument_id 없음: {instrument_id}")
        raise ConcurrencyConflictError(
            f"instruments.instrument_id={instrument_id}: lifecycle_state가 기대값"
            f"({expected_state.value})과 다릅니다(동시 처리 충돌) — 다시 조회 후 시도하세요."
        )

    async def get_listing(
        self, conn: asyncpg.Connection, venue: Venue, venue_symbol: str, at: AwareDatetime
    ) -> VenueListing | None:
        row = await conn.fetchrow(
            "SELECT * FROM venue_listings "
            "WHERE venue = $1 AND venue_symbol = $2 "
            "AND listed_at <= $3 AND (delisted_at IS NULL OR $3 < delisted_at)",
            venue.value,
            venue_symbol,
            at,
        )
        return None if row is None else _row_to_listing(row)

    async def add_listing(self, conn: asyncpg.Connection, listing: VenueListing) -> VenueListing:
        try:
            row = await conn.fetchrow(
                "INSERT INTO venue_listings "
                "(instrument_id, venue, venue_symbol, listed_at, delisted_at, is_primary) "
                "VALUES ($1,$2,$3,$4,$5,$6) RETURNING *",
                listing.instrument_id,
                listing.venue.value,
                listing.venue_symbol,
                listing.listed_at,
                listing.delisted_at,
                listing.is_primary,
            )
        except asyncpg.exceptions.ExclusionViolationError as exc:
            raise VenueListingOverlapError(
                f"겹치는 상장 기간: venue={listing.venue.value} "
                f"venue_symbol={listing.venue_symbol}"
            ) from exc
        return _row_to_listing(row)
