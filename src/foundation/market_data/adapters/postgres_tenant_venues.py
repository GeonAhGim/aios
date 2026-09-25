"""LA-24 — asyncpg implementation of `VenueRegistrySource` (ports/entitlement.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

DC-8 (9049e2b6b0b7): reads only the set of non-expired `venue` rows from the
`entitlements` table for a tenant. No write path (issuing entitlements is
out of scope for this leaf). No decision logic —
`PaperTenantVenueEntitlement` uses this set for its entitlement check.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.foundation.market_data.contracts.v1 import Venue

__all__ = ["PostgresTenantVenueSource"]


class PostgresTenantVenueSource:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def registered_venues(self, tenant_id: UUID) -> frozenset[Venue]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT venue FROM entitlements "
                "WHERE tenant_id = $1 AND (expires_at IS NULL OR expires_at > now())",
                tenant_id,
            )
        return frozenset(Venue(row["venue"]) for row in rows)
