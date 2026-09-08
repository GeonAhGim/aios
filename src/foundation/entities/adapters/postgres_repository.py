"""FA-2 — 엔티티 계층 4테이블의 asyncpg 저장소 구현.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2
(§2.1 표·§3 계약), 105번(동시성 표준).

tenant 격리(decision: LA-22/PLT-27과 동일 패턴) — `LegalEntity`만
`tenant_id` 컬럼을 갖는다(FA-1 계약과 동일, Fund/Portfolio/SubAccount는
자기 tenant_id가 없다). 하위 3개 테이블 조회는 `legal_entity`까지
JOIN을 타고 `WHERE le.tenant_id = $1`을 반드시 건다 — `tenant_id`는
선택 파라미터가 아니라 모든 조회·폐쇄 메서드의 첫 필수 인자다. 존재하지
않는 id와 다른 tenant 소유 id는 항상 같은 `None`으로 접는다(404 동형,
호출부가 둘을 구분할 방법이 없다).

폐쇄(`closed_at`)는 조건부 UPDATE로만 쓴다 — `closed_at IS NULL`을 기대
상태로 걸어 동시 이중 폐쇄를 막는다(105번). 상위 폐쇄(legal_entity/fund/
portfolio)는 같은 UPDATE 문 안에 `NOT EXISTS(활성 자식)` 절을 추가로 건다
— 사전 SELECT(도메인 `validate_close_*`용 `list_*_by_*` 호출)와 이 UPDATE
사이에 다른 트랜잭션이 활성 자식을 INSERT하는 TOCTOU 경합을 막기 위함이다.
tenant 소유권을 EXISTS로 원자화한 기법을 그대로 확장한 것 — 도메인 판정
(`domain/hierarchy.py`)은 사전검사로만 남고 재구현하지 않는다, 이 DB 절이
최종 권위다. 조건부 UPDATE가 0행이면 "존재하지 않음/교차 테넌트"·"이미
폐쇄됨(경합)"·"활성 자식 존재(계층 위반)"를 구분해야 하므로
`activate_revision()`(mandates 어댑터)과 같은 방식으로 재조회해 갈라
던진다.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio, SubAccount
from src.foundation.entities.domain.hierarchy import HierarchyViolationError


def _row_to_legal_entity(row: asyncpg.Record) -> LegalEntity:
    return LegalEntity(
        entity_id=row["entity_id"],
        tenant_id=row["tenant_id"],
        name=row["name"],
        jurisdiction=row["jurisdiction"],
        region_tag=row["region_tag"],
        closed_at=row["closed_at"],
    )


def _row_to_fund(row: asyncpg.Record) -> Fund:
    return Fund(
        fund_id=row["fund_id"],
        entity_id=row["entity_id"],
        base_currency=row["base_currency"],
        mandate_ref=row["mandate_ref"],
        inception=row["inception"],
        closed_at=row["closed_at"],
    )


def _row_to_portfolio(row: asyncpg.Record) -> Portfolio:
    return Portfolio(
        portfolio_id=row["portfolio_id"],
        fund_id=row["fund_id"],
        venue_account_ref=row["venue_account_ref"],
        closed_at=row["closed_at"],
    )


def _row_to_sub_account(row: asyncpg.Record) -> SubAccount:
    return SubAccount(
        sub_account_id=row["sub_account_id"],
        portfolio_id=row["portfolio_id"],
        owner_ref=row["owner_ref"],
        closed_at=row["closed_at"],
    )


class PostgresEntityRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- LegalEntity ------------------------------------------------------

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
        return _row_to_legal_entity(row)

    async def get_legal_entity(self, tenant_id: UUID, entity_id: UUID) -> LegalEntity | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM legal_entity WHERE tenant_id = $1 AND entity_id = $2",
                tenant_id,
                entity_id,
            )
        return _row_to_legal_entity(row) if row is not None else None

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
        return _row_to_legal_entity(row)

    async def list_funds_by_entity(self, tenant_id: UUID, entity_id: UUID) -> list[Fund]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT f.* FROM fund f "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND f.entity_id = $2",
                tenant_id,
                entity_id,
            )
        return [_row_to_fund(row) for row in rows]

    # -- Fund ---------------------------------------------------------------

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
        return _row_to_fund(row)

    async def get_fund(self, tenant_id: UUID, fund_id: UUID) -> Fund | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT f.* FROM fund f "
                "JOIN legal_entity le ON le.entity_id = f.entity_id "
                "WHERE le.tenant_id = $1 AND f.fund_id = $2",
                tenant_id,
                fund_id,
            )
        return _row_to_fund(row) if row is not None else None

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
        return _row_to_fund(row)

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
        return [_row_to_portfolio(row) for row in rows]

    # -- Portfolio ------------------------------------------------------------

    async def create_portfolio(self, portfolio: Portfolio) -> Portfolio:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO portfolio (portfolio_id, fund_id, venue_account_ref) "
                "VALUES ($1, $2, $3) RETURNING *",
                portfolio.portfolio_id,
                portfolio.fund_id,
                portfolio.venue_account_ref,
            )
        return _row_to_portfolio(row)

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
        return _row_to_portfolio(row) if row is not None else None

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
        return _row_to_portfolio(row)

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
        return [_row_to_sub_account(row) for row in rows]

    # -- SubAccount -----------------------------------------------------------

    async def create_sub_account(self, sub_account: SubAccount) -> SubAccount:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO sub_account (sub_account_id, portfolio_id, owner_ref) "
                "VALUES ($1, $2, $3) RETURNING *",
                sub_account.sub_account_id,
                sub_account.portfolio_id,
                sub_account.owner_ref,
            )
        return _row_to_sub_account(row)

    async def get_sub_account(
        self, tenant_id: UUID, sub_account_id: UUID
    ) -> SubAccount | None:
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
        return _row_to_sub_account(row) if row is not None else None

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
        return _row_to_sub_account(row)


__all__ = ["PostgresEntityRepository", "ConcurrencyConflictError"]
