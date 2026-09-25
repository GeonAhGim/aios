"""LA-9 — Candle/tick/quarantine storage and retrieval port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2, §9.2 LA-9.

domain/application knows only this Protocol; the actual implementation
(adapters/postgres_candle_store.py, LA-13) is unknown (71 §4). `conn` expresses
only the contract that the caller passes an already-opened `asyncpg.Connection`
(same pattern as LC-8a `src/foundation/ledger/ports/*.py`).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, QualityIssue, SeriesKey
from src.foundation.market_data.domain.candle_columns import CandleColumns


@runtime_checkable
class CandleStore(Protocol):
    async def upsert_batch(
        self, conn: asyncpg.Connection, batch_id: UUID, candles: list[CandleRecord]
    ) -> int:
        """§5 `ON CONFLICT (venue, instrument_id, timeframe, open_time) DO
        NOTHING` — returns the count of actually inserted rows (0 on retry
        is not an error)."""
        ...

    async def quarantine(
        self,
        conn: asyncpg.Connection,
        batch_id: UUID,
        candles: list[CandleRecord],
        issues: list[QualityIssue],
    ) -> None:
        """Store quarantined candles with judgment reasons (issues) in
        `md_quarantine_candle` — do not write to the normal table."""
        ...

    async def query(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> list[CandleRecord]:
        """Query only batches saved before `as_of` (A5 determinism). If
        `as_of=None`, return the latest saved state."""
        ...

    async def last_open_time(
        self, conn: asyncpg.Connection, key: SeriesKey
    ) -> AwareDatetime | None:
        """Returns `None` if no candles exist (used by the scheduler to
        determine the first backfill range)."""
        ...

    async def read_candles_columnar(
        self,
        conn: asyncpg.Connection,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> CandleColumns:
        """LA-23b (ADR-2026-09-04-A #1) — Returns a column array sorted by
        `open_time ASC` with the same filters as `query()` (including `as_of`
        snapshot). An internal-only path for bulk consumers (replay,
        `get_candles.load_series`) to iterate without per-record pydantic
        validation (`domain/candle_columns.to_candle_records`) — does not
        replace `query()` (for small queries, `query()` remains simpler, e.g.,
        mark price lookup in the positions context)."""
        ...
