"""LB-7 — Daily NAV repository port.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §4.3, §9 LB-7.

The domain/application layer knows only this Protocol; the concrete implementation
(adapters/postgres_nav_repository.py, LB-9) is hidden. `pos_nav_daily` follows WORM
(§9 LB-8) — inserts only, no updates.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.positions.contracts.v1 import NAVSnapshot


@runtime_checkable
class NavRepository(Protocol):
    async def insert(self, conn: asyncpg.Connection, nav: NAVSnapshot) -> NAVSnapshot:
        """The adapter raises an exception on `(account_id, nav_date)` UNIQUE violation —
        the caller (`compute_daily_nav`, LB-15) must check idempotency via `get`
        before recomputing for the same day."""
        ...

    async def get(
        self, conn: asyncpg.Connection, account_id: UUID, nav_date: date
    ) -> NAVSnapshot | None:
        """Returns `None` if no NAV exists for that date."""
        ...
