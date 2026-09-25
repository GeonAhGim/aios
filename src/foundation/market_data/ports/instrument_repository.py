"""DC-5 — Symbol master (`Instrument`/`VenueListing`) persistence port.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-5, §3.2(contract), §4.1·§4.2(invariants), §9.2 DC-5.

domain/application knows only this Protocol and not the actual
implementation (adapters/storage/*) (71 §4). Reuses `contracts/v2/instruments`(DC-1) as-is —
`instrument_id` is immutable, and the prohibition on overlapping `venue_listings` periods (§4.1) is
actually enforced by the DB EXCLUDE constraint (DC-4); this Protocol
expresses only the contract shape.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import Venue
from src.foundation.market_data.contracts.v2.instruments import (
    Instrument,
    InstrumentLifecycle,
    VenueListing,
)


@runtime_checkable
class InstrumentRepository(Protocol):
    async def get(self, conn: asyncpg.Connection, instrument_id: str) -> Instrument | None:
        """`None` if not found."""
        ...

    async def create(self, conn: asyncpg.Connection, instrument: Instrument) -> Instrument:
        """Issue a new record. The adapter raises on re-insertion of the same
        `instrument_id` (§4.1 `instrument_id` immutable — no UPDATE path on this method)."""
        ...

    async def update_lifecycle_state(
        self,
        conn: asyncpg.Connection,
        instrument_id: str,
        *,
        expected_state: InstrumentLifecycle,
        state: InstrumentLifecycle,
    ) -> Instrument:
        """§4.2 Only results that have already passed the transition table arrive here —
        validation of the transition itself is DC-3's (`domain/instruments/lifecycle.py`)
        responsibility; this method only persists.

        `expected_state` must be passed as-is from the caller's current state read
        just before the transition decision (105 concurrency standard) — the adapter uses
        it as the WHERE condition of the UPDATE, and rejects with
        `ConcurrencyConflictError` (fail-closed) if another transaction changed the
        state between the decision and the write."""
        ...

    async def get_listing(
        self, conn: asyncpg.Connection, venue: Venue, venue_symbol: str, at: AwareDatetime
    ) -> VenueListing | None:
        """Listing valid at `at` (`listed_at <= at` and `delisted_at` is `NULL` or
        after `at`). `None` if not found."""
        ...

    async def add_listing(self, conn: asyncpg.Connection, listing: VenueListing) -> VenueListing:
        """Symbol changes use the approach of filling `delisted_at` on the old listing then
        adding a new one (§3.2) — this method only performs the addition; the caller
        closes the old listing separately."""
        ...
