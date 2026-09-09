"""공용 FA-1 기본 엔티티 계층(법인/펀드/포트폴리오) 시드 헬퍼.

FA-0d(`cdb114b6903f`, task-1943)는 `pos_snapshot.portfolio_id`가 NULL인 행이
하나라도 있으면 마이그레이션 전체를 그 자리에서 실패시킨다(역산 불가,
docstring 참고) — 옳은 fail-closed 동작이다. 문제는 이 규칙을 모르고
`PositionKey(portfolio_id=uuid4(), ...)`처럼 아무 UUID나 채워 넣는 테스트
픽스처다: 그 값은 `position_key` 문자열 안에만 있을 뿐 `pos_snapshot.
portfolio_id` 컬럼에는 반영되지 않아(어댑터가 그 컬럼을 아직 안 씀) 컬럼이
NULL로 남고, 그 행은 테스트가 끝나도 지워지지 않는다. 같은 pytest 세션
안에서 다른 테스트가 `cdb114b6903f`를 downgrade→upgrade 왕복시키면(risk_gate
R-34 롤백 테스트 등) 그 좀비 행을 만나 실패한다(task-2543).

해법은 백필이 아니라 시드다 — `pos_snapshot` 행을 만드는 모든 테스트가
먼저 이 헬퍼로 tenant의 실제 기본 포트폴리오를 만들고, 그 `portfolio_id`를
써야 한다."""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import asyncpg

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.contracts.v1 import Fund, LegalEntity, Portfolio
from src.foundation.entities.domain.defaults import (
    default_entity_id,
    default_fund_id,
    default_portfolio_id,
)


async def bootstrap_default_portfolio(pool: asyncpg.Pool, tenant_id: UUID) -> UUID:
    """`tenant_id`의 FA-1 기본 법인/펀드/포트폴리오를 만들고 portfolio_id를
    돌려준다. `tenant_id`는 매 호출마다 새로 만든 tenant여야 한다(create_*가
    멱등이 아니라 이미 있는 tenant에 두 번 부르면 중복 키로 실패한다)."""
    repo = PostgresEntityRepository(pool)
    entity = await repo.create_legal_entity(
        LegalEntity(
            entity_id=default_entity_id(tenant_id),
            tenant_id=tenant_id,
            name="Test Default Entity",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    fund = await repo.create_fund(
        Fund(
            fund_id=default_fund_id(tenant_id),
            entity_id=entity.entity_id,
            base_currency=Currency.USDT,
            inception=date(2026, 1, 1),
        )
    )
    portfolio = await repo.create_portfolio(
        Portfolio(
            portfolio_id=default_portfolio_id(tenant_id),
            fund_id=fund.fund_id,
            venue_account_ref=f"test-default-venue-{uuid4().hex[:8]}",
        )
    )
    return portfolio.portfolio_id
