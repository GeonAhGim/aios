"""LA-9 — Instrument/alias/corporate action reference data repository port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application knows only this Protocol; the actual implementation
(adapters/postgres_reference_repository.py, LA-12) does not (71 §4).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.foundation.market_data.contracts.v1 import (
    CorporateAction,
    InstrumentRef,
    RegisterInstrumentCommand,
    SymbolStatus,
    Venue,
)


@runtime_checkable
class ReferenceRepository(Protocol):
    async def get_instrument(
        self, conn: asyncpg.Connection, venue: Venue, canonical: str, at: AwareDatetime
    ) -> InstrumentRef | None:
        """`at` instrument valid at that time (including aliases). Returns `None` if not found."""
        ...

    async def register(
        self, conn: asyncpg.Connection, cmd: RegisterInstrumentCommand
    ) -> InstrumentRef:
        """Register new instrument. Adapter raises if (venue, venue_symbol) already exists —
        state transitions are handled by a separate leaf (lifecycle)."""
        ...

    async def add_alias(
        self, conn: asyncpg.Connection, instrument_id: UUID, venue: Venue, venue_symbol: str
    ) -> None:
        """Preserves past symbols from RENAME etc. so they remain queryable (A3)."""
        ...

    async def list_actions(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        """Ordered by `ex_date` ascending. Returns empty list if none."""
        ...

    async def record_action(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        """§5 Idempotent on `(instrument_id, action_type, ex_date)` — if already exists,
        compares digest and returns the existing value unchanged (re-execution does not
        create a new row)."""
        ...


class SymbolAliasRef(BaseModel):
    """LA-24 — Read representation of one `md_symbol_alias` row. `contracts/v1.py` already
    hits the 300-line cap (P6.line_cap), so the port defines the read contract directly,
    same pattern as `ports/coverage_repository.CoverageSpan`."""

    alias_id: UUID
    instrument_id: UUID
    venue: Venue
    alias_symbol: str
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None


@runtime_checkable
class ReferenceReadRepository(Protocol):
    """LA-24 — Read-only port for HTTP API.
    `ReferenceRepository` (above, LA-9) remains the collect/register contract
    (existing fake/isinstance tests unchanged); list/alias/id queries are separated here.
    Implementation: adapters/postgres_reference_reader.py."""

    async def get_by_id(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> InstrumentRef | None:
        """Direct lookup by `md_instrument.instrument_id`. Returns `None` if not found."""
        ...

    async def list_instruments(
        self,
        conn: asyncpg.Connection,
        *,
        venues: frozenset[Venue],
        status: SymbolStatus | None,
        after: UUID | None,
        limit: int,
    ) -> list[InstrumentRef]:
        """Up to `limit` instruments within `venues`, keyed by
        (venue, canonical_symbol, instrument_id) ascending keyset.
        `after` is the last `instrument_id` from the previous page (cursor).
        Returns empty list when `venues` is empty (fail-closed)."""
        ...

    async def list_aliases(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[SymbolAliasRef]:
        """Ordered by `valid_from` ascending. Returns empty list if none."""
        ...
