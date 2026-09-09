"""FA-2 -- SubAccount repository methods (one of PostgresEntityRepository's 4 mixins).

Split out per entity level because of the 300-line cap (P6) -- not used
standalone; `postgres_repository.PostgresEntityRepository` composes all 4
mixins. See that file's module docstring for the concurrency/tenant
isolation contract. Being the lowest level, it has no `list_*_by_*`/
`NOT EXISTS` child check."""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.adapters._rows import row_to_sub_account
from src.foundation.entities.contracts.v1 import SubAccount


class SubAccountRepositoryMixin:
    _pool: asyncpg.Pool

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO sub_account (sub_account_id, portfolio_id, owner_ref) "
                "VALUES ($1, $2, $3) RETURNING *",
                sub_account.sub_account_id,
                sub_account.portfolio_id,
                sub_account.owner_ref,
            )
        return row_to_sub_account(row)

    async def get_sub_account(self, tenant_id: UUID, sub_account_id: UUID) -> SubAccount | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT s.* FROM sub_account s "
                "JOIN portfolio p ON p.portfolio_id = s.portfolio_id "
                "JOIN fund f ON f.fund_id = p.fund_id "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND s.sub_account_id = $2",
                tenant_id,
                sub_account_id,
            )
        return row_to_sub_account(row) if row is not None else None

    async def close_sub_account(
        self, tenant_id: UUID, sub_account_id: UUID, *, closed_at: datetime
    ) -> SubAccount:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE sub_account SET closed_at = $1 "
                "WHERE sub_account_id = $2 AND closed_at IS NULL "
                "AND EXISTS (SELECT 1 FROM portfolio p "
                "JOIN fund f ON f.fund_id = p.fund_id "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE p.portfolio_id = sub_account.portfolio_id AND le.tenant_id = $3) "
                "RETURNING *",
                closed_at,
                sub_account_id,
                tenant_id,
            )
            if row is None:
                if await self.get_sub_account(tenant_id, sub_account_id) is None:
                    raise LookupError(f"존재하지 않는 SubAccount입니다: {sub_account_id}")
                raise ConcurrencyConflictError(
                    f"sub_account.sub_account_id={sub_account_id}: "
                    "다른 요청이 먼저 폐쇄했습니다."
                )
        return row_to_sub_account(row)
