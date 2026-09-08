"""RD-20 — Corporate action filing storage port (append-only).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-20.

`append` never performs an UPDATE — a corrective filing goes in as a new
row with a different `source_ref` (DART receipt number), even for the
same `(instrument_id, action_type, ex_date)`. `source_ref` is the
repository's idempotency key: inserting the same filing (same receipt
number) twice does not create a new row, it just returns the existing
row unchanged.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import CorporateAction

__all__ = ["CorporateActionFilingRepository"]


@runtime_checkable
class CorporateActionFilingRepository(Protocol):
    async def append(
        self, conn: asyncpg.Connection, action: CorporateAction
    ) -> CorporateAction:
        """Idempotent on `source_ref` — if it already exists, the existing value
        is returned unchanged (no new row is created). A different `source_ref`
        (a corrective filing) always results in a new row."""
        ...

    async def list_history(
        self, conn: asyncpg.Connection, instrument_id: UUID
    ) -> list[CorporateAction]:
        """Full history in ascending `known_at` order — includes every row
        accumulated through corrections. Point-in-time interpretation is
        handled by `domain/corporate_action/point_in_time.py` (this port
        simply returns the stored facts as-is)."""
        ...
