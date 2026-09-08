"""FA-2 -- LegalEntity 저장소 메서드(PostgresEntityRepository 믹스인 4개 중 하나).

300줄 상한(P6) 때문에 레벨별로 분리했다 -- 단독으로 쓰지 않는다,
`postgres_repository.PostgresEntityRepository`가 4개 믹스인을 합성한다.
동시성/tenant 격리 규약은 그 파일 모듈 docstring 참고.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.adapters._rows import row_to_fund, row_to_legal_entity
from src.foundation.entities.contracts.v1 import Fund, LegalEntity
from src.foundation.entities.domain.hierarchy import HierarchyViolationError


class LegalEntityRepositoryMixin:
    _pool: asyncpg.Pool

    async def create_legal_entity(self, entity: LegalEntity) -> LegalEntity:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO legal_entity (entity_id, tenant_id, name, jurisdiction, region_tag) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING *",
                entity.entity_id,
                entity.tenant_id,
                entity.name,
                entity.jurisdiction,
                entity.region_tag,
            )
        return row_to_legal_entity(row)

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM legal_entity WHERE tenant_id = $1 AND entity_id = $2",
                tenant_id,
                entity_id,
            )
        return row_to_legal_entity(row) if row is not None else None

    async def close_legal_entity(
        self, tenant_id: UUID, entity_id: UUID, *, closed_at: datetime
    ) -> LegalEntity:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "UPDATE legal_entity SET closed_at = $1 "
                "WHERE entity_id = $2 AND tenant_id = $3 AND closed_at IS NULL "
                "AND NOT EXISTS (SELECT 1 FROM fund f "
                "WHERE f.entity_id = legal_entity.entity_id AND f.closed_at IS NULL) "
                "RETURNING *",
                closed_at,
                entity_id,
                tenant_id,
            )
            if row is None:
                existing = await self.get_legal_entity(tenant_id, entity_id)
                if existing is None:
                    raise LookupError(f"존재하지 않는 LegalEntity입니다: {entity_id}")
                if existing.closed_at is not None:
                    raise ConcurrencyConflictError(
                        f"legal_entity.entity_id={entity_id}: 다른 요청이 먼저 폐쇄했습니다."
                    )
                raise HierarchyViolationError(
                    f"LegalEntity {entity_id} 폐쇄 불가 — 활성 Fund가 남아 있습니다."
                )
        return row_to_legal_entity(row)

    async def list_funds_by_entity(self, tenant_id: UUID, entity_id: UUID) -> list[Fund]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT f.* FROM fund f "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND f.entity_id = $2",
                tenant_id,
                entity_id,
            )
        return [row_to_fund(row) for row in rows]
