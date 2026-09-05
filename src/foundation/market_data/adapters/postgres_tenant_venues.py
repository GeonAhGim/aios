"""LA-24 — `VenueRegistrySource`(ports/entitlement.py)의 asyncpg 구현.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9.2 LA-24.

DC-8(9049e2b6b0b7) `entitlements` 테이블에서 테넌트의 미만료 행 `venue`
집합만 읽는다. 쓰기 경로는 없다(이용권 발급은 이 리프 범위 밖). 판정
로직은 갖지 않는다 — `PaperTenantVenueEntitlement`가 이 집합으로 판정한다.
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
