"""RD-5 -- entity link repository port (list unlinked items, persist link outcomes).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 2.2
ports table, Sec 9 RD-5 DoD (f).

Pure interface only -- RD-4 owns the migration and Postgres adapter. This
leaf never touches a database; tests exercise it through an in-memory
fake.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.entity_link import EntityLinkResult

__all__ = ["EntityLinkRepository"]


@runtime_checkable
class EntityLinkRepository(Protocol):
    async def list_unlinked(
        self, conn: asyncpg.Connection, *, tenant_id: UUID, limit: int
    ) -> list[ResearchItem]:
        """Items with no saved `instrument_id` link yet. An item previously
        left `NOT_FOUND`/`NO_DETERMINISTIC_KEY` stays eligible here -- only a
        successful match removes it from this pool."""
        ...

    async def save_links(
        self, conn: asyncpg.Connection, results: Sequence[EntityLinkResult]
    ) -> None:
        """Persist every outcome (mapped and unmapped). Idempotent per
        `item_id` -- re-saving the same result has no additional effect."""
        ...
