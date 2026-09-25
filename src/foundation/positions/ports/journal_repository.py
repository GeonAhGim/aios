"""LB-7 — Position journal repository port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

The domain/application layer knows only this Protocol; the concrete implementation
(adapters/postgres_journal_repository.py, LB-9) is opaque to it (71st §4).
This file performs no I/O itself — the `conn` parameter is a contract that the
caller passes an already-opened `asyncpg.Connection` as-is (same pattern as
LC-8a `src/foundation/ledger/ports/journal_repository.py`). The `append` method
assumes the adapter will fill in as-yet-unassigned fields (`sequence_no`/`prev_hash`/
`entry_hash`/`id`/`recorded_at`) under an advisory lock scoped to `position_key`,
so those fields appear only in the returned `PositionJournalEntryView`, not as inputs.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Money
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView


@runtime_checkable
class PositionJournalRepository(Protocol):
    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        position_key: str,
        entry_type: JournalEntryType,
        qty_delta: Decimal,
        price: Money | None,
        fee: Money | None,
        realized_pnl_base: Decimal,
        fx_rate: Decimal | None,
        fx_source: str | None,
        source_event_type: str,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: AwareDatetime,
    ) -> PositionJournalEntryView:
        """§4.3 Append a journal entry. If `idempotency_key` already exists, return
        the existing view without writing a new entry (POS_IDEMPOTENT_REPLAY, not an
        error) — raise POS_IDEMPOTENCY_DIGEST_MISMATCH if the digest differs. Commit
        responsibility lies with the caller (the application leaf that continues
        snapshot updates on the same `conn`)."""
        ...

    async def list_for(
        self, conn: asyncpg.Connection, position_key: str, from_seq: int = 0
    ) -> list[PositionJournalEntryView]:
        """Return entries with `sequence_no > from_seq` in ascending order (for
        rebuild and integrity verification). Returns an empty list if none exist."""
        ...

    async def last(
        self, conn: asyncpg.Connection, position_key: str
    ) -> PositionJournalEntryView | None:
        """Return the most recent entry (maximum `sequence_no`). Returns `None` if
        the journal is empty (the first entry has `prev_hash=None`)."""
        ...
