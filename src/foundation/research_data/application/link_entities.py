"""RD-5 -- application/link_entities.py: re-run entity linking over unlinked items.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md Sec 2.2
application/link_entities.py, Sec 9 RD-5.

Pulls the current unlinked batch from `EntityLinkRepository.list_unlinked`,
runs the pure `domain.entity_link.link_item` over each one, and persists
every outcome (mapped or still-unmapped) via `save_links`. Idempotent:
once an item is mapped, the repository stops returning it from
`list_unlinked`, so re-running with nothing new to link saves an empty
batch.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.research_data.domain.entity_link import (
    EntityLinkResult,
    EntityResolver,
    link_item,
)
from src.foundation.research_data.ports.entity_link_repository import EntityLinkRepository

__all__ = ["link_entities"]


async def link_entities(
    conn: asyncpg.Connection,
    repo: EntityLinkRepository,
    resolver: EntityResolver,
    *,
    tenant_id: UUID,
    limit: int = 500,
) -> list[EntityLinkResult]:
    items = await repo.list_unlinked(conn, tenant_id=tenant_id, limit=limit)
    results = [link_item(item, resolver) for item in items]
    await repo.save_links(conn, results)
    return results
