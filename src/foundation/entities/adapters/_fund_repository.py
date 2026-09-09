"""FA-2 -- Fund repository methods (one of PostgresEntityRepository's 4 mixins).

Split out per entity level because of the 300-line cap (P6) -- not used
standalone; `postgres_repository.PostgresEntityRepository` composes all 4
mixins. See that file's module docstring for the concurrency/tenant
isolation contract.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.adapters._rows import row_to_fund, row_to_portfolio
from src.foundation.entities.contracts.v1 import Fund, Portfolio
from src.foundation.entities.domain.hierarchy import HierarchyViolationError


class FundRepositoryMixin:
    _pool: asyncpg.Pool

    async def create_fund(self, fund: Fund) -> Fund:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO fund (fund_id, entity_id, base_currency, mandate_ref, inception) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                fund.fund_id,
                fund.entity_id,
                fund.base_currency.value,
                fund.mandate_ref,
                fund.inception,
            )
        return row_to_fund(row)

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT f.* FROM fund f "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND f.fund_id = $2",
                tenant_id,
                fund_id,
            )
        return row_to_fund(row) if row is not None else None

    async def close_fund(self, tenant_id: UUID, fund_id: UUID, *, closed_at: datetime) -> Fund:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE fund SET closed_at = $1 "
                "WHERE fund_id = $2 AND closed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM legal_entity le "
                "WHERE le.entity_id = fund.entity_id AND le.tenant_id = $3) "
                "AND NOT EXISTS (SELECT 1 FROM portfolio p "
                "WHERE p.fund_id = fund.fund_id AND p.closed_at IS NULL) "
                "RETURNING *",
                closed_at,
                fund_id,
                tenant_id,
            )
            if row is None:
                existing = await self.get_fund(tenant_id, fund_id)
                if existing is None:
                    raise LookupError(f"존재하지 않는 Fund입니다: {fund_id}")
                if existing.closed_at is not None:
                    raise ConcurrencyConflictError(
                        f"fund.fund_id={fund_id}: 다른 요청이 먼저 폐쇄했습니다."
                    )
                raise HierarchyViolationError(
                    f"Fund {fund_id} 폐쇄 불가 — 활성 Portfolio가 남아 있습니다."
                )
        return row_to_fund(row)

    async def list_portfolios_by_fund(self, tenant_id: UUID, fund_id: UUID) -> list[Portfolio]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT p.* FROM portfolio p "
                "JOIN fund f ON f.fund_id = p.fund_id "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND p.fund_id = $2",
                tenant_id,
                fund_id,
            )
        return [row_to_portfolio(row) for row in rows]
