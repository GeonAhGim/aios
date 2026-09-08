"""FA-2 -- Portfolio 저장소 메서드(PostgresEntityRepository 믹스인 4개 중 하나).

300줄 상한(P6) 때문에 레벨별로 분리했다 -- 단독으로 쓰지 않는다,
`postgres_repository.PostgresEntityRepository`가 4개 믹스인을 합성한다.
동시성/tenant 격리 규약은 그 파일 모듈 docstring 참고.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.adapters._rows import row_to_portfolio, row_to_sub_account
from src.foundation.entities.contracts.v1 import Portfolio, SubAccount
from src.foundation.entities.domain.hierarchy import HierarchyViolationError


class PortfolioRepositoryMixin:
    _pool: asyncpg.Pool

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO portfolio (portfolio_id, fund_id, venue_account_ref) "
                "VALUES ($1, $2, $3) RETURNING *",
                portfolio.portfolio_id,
                portfolio.fund_id,
                portfolio.venue_account_ref,
            )
        return row_to_portfolio(row)

    async def get_portfolio(self, tenant_id: UUID, portfolio_id: UUID) -> Portfolio | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT p.* FROM portfolio p "
                "JOIN fund f ON f.fund_id = p.fund_id "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND p.portfolio_id = $2",
                tenant_id,
                portfolio_id,
            )
        return row_to_portfolio(row) if row is not None else None

    async def close_portfolio(
        self, tenant_id: UUID, portfolio_id: UUID, *, closed_at: datetime
    ) -> Portfolio:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE portfolio SET closed_at = $1 "
                "WHERE portfolio_id = $2 AND closed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM fund f "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE f.fund_id = portfolio.fund_id AND le.tenant_id = $3) "
                "AND NOT EXISTS (SELECT 1 FROM sub_account s "
                "WHERE s.portfolio_id = portfolio.portfolio_id AND s.closed_at IS NULL) "
                "RETURNING *",
                closed_at,
                portfolio_id,
                tenant_id,
            )
            if row is None:
                existing = await self.get_portfolio(tenant_id, portfolio_id)
                if existing is None:
                    raise LookupError(f"존재하지 않는 Portfolio입니다: {portfolio_id}")
                if existing.closed_at is not None:
                    raise ConcurrencyConflictError(
                        f"portfolio.portfolio_id={portfolio_id}: 다른 요청이 먼저 폐쇄했습니다."
                    )
                raise HierarchyViolationError(
                    f"Portfolio {portfolio_id} 폐쇄 불가 — 활성 SubAccount가 남아 있습니다."
                )
        return row_to_portfolio(row)

    async def list_sub_accounts_by_portfolio(
        self, tenant_id: UUID, portfolio_id: UUID
    ) -> list[SubAccount]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT s.* FROM sub_account s "
                "JOIN portfolio p ON p.portfolio_id = s.portfolio_id "
                "JOIN fund f ON f.fund_id = p.fund_id "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND s.portfolio_id = $2",
                tenant_id,
                portfolio_id,
            )
        return [row_to_sub_account(row) for row in rows]
