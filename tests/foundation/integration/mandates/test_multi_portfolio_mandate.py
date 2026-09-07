"""FA-0b DoD — 한 테넌트가 포트폴리오 2개를 갖고 각각 독립적으로 mandate를
activate할 수 있어야 한다. `47ec4b178f54`(UNIQUE(tenant_id) ->
UNIQUE(tenant_id, portfolio_id)) 이전 스키마에서는 두 번째
`get_or_create_mandate` 호출이 UniqueViolationError로 죽는다 — 이 마이그레이션을
되돌리면(`alembic downgrade -1`) 이 테스트가 실제로 FAIL한다(수동 확인,
task-1941 note)."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.domain.models import Autonomy, MandateRevision, MandateRevisionState
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMandateRepository(pool)


async def _cleanup_mandates(pool: asyncpg.Pool, mandate_ids: list[UUID]) -> None:
    """이 파일의 테스트는 의도적으로 같은 tenant_id에 mandate 2개(포트폴리오별)를
    만든다 — `portfolio_mandate_tenant_id_key`(옛 UNIQUE(tenant_id))가 살아있던
    시절엔 있을 수 없던 조합이라, 정리하지 않고 공유 TEST_DATABASE_URL에 남기면
    이 스키마 이전 시점까지 되돌아가는 다른 마이그레이션 왕복 테스트(예:
    test_risk_gate_lifecycle.py의 downgrade round-trip)가 남의 중복 tenant_id
    때문에 영구히 깨진다."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE portfolio_mandate SET active_revision_id = NULL WHERE id = ANY($1::uuid[])",
            mandate_ids,
        )
        await conn.execute(
            "DELETE FROM mandate_revision WHERE mandate_id = ANY($1::uuid[])", mandate_ids
        )
        await conn.execute(
            "DELETE FROM portfolio_mandate WHERE id = ANY($1::uuid[])", mandate_ids
        )


def _draft_revision() -> MandateRevision:
    return MandateRevision(
        id=uuid4(),
        mandate_id=uuid4(),  # insert_draft_revision()이 mandate_id를 별도 인자로 받아 덮어씀
        revision_no=1,
        state=MandateRevisionState.DRAFT,
        max_total_exposure_pct=80.0,
        max_single_instrument_pct=20.0,
        min_cash_buffer_pct=5.0,
        max_daily_loss_pct=3.0,
        allowed_autonomy=Autonomy.PAPER,
    )


async def test_two_portfolios_per_tenant_each_get_independent_mandate(pool, repo):
    tenant_id = await create_test_tenant(pool)
    portfolio_a = uuid4()
    portfolio_b = uuid4()

    mandate_a = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_a)
    mandate_b = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_b)
    try:
        assert mandate_a.id != mandate_b.id
        assert mandate_a.portfolio_id == portfolio_a
        assert mandate_b.portfolio_id == portfolio_b

        fetched_a = await repo.get_mandate(tenant_id, portfolio_a)
        fetched_b = await repo.get_mandate(tenant_id, portfolio_b)
        assert fetched_a is not None and fetched_a.id == mandate_a.id
        assert fetched_b is not None and fetched_b.id == mandate_b.id
    finally:
        await _cleanup_mandates(pool, [mandate_a.id, mandate_b.id])


async def test_two_portfolios_per_tenant_each_activate_independently(pool, repo):
    tenant_id = await create_test_tenant(pool)
    portfolio_a = uuid4()
    portfolio_b = uuid4()

    mandate_a = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_a)
    mandate_b = await repo.get_or_create_mandate(tenant_id, tenant_id, portfolio_id=portfolio_b)
    try:
        draft_a = await repo.insert_draft_revision(
            mandate_id=mandate_a.id, revision_no=1, rules=_draft_revision()
        )
        draft_b = await repo.insert_draft_revision(
            mandate_id=mandate_b.id, revision_no=1, rules=_draft_revision()
        )

        activated_a = await repo.activate_revision(
            mandate_a.id, draft_a.id, expected_active_revision_id=None
        )
        activated_b = await repo.activate_revision(
            mandate_b.id, draft_b.id, expected_active_revision_id=None
        )

        assert activated_a.state == MandateRevisionState.ACTIVE
        assert activated_b.state == MandateRevisionState.ACTIVE

        active_a = await repo.get_active_revision(mandate_a.id)
        active_b = await repo.get_active_revision(mandate_b.id)
        assert active_a is not None and active_a.id == draft_a.id
        assert active_b is not None and active_b.id == draft_b.id
    finally:
        await _cleanup_mandates(pool, [mandate_a.id, mandate_b.id])


async def test_get_mandate_without_portfolio_id_resolves_default_portfolio(pool, repo):
    """`portfolio_id`를 안 넘기는 기존(단일 포트폴리오) 호출부는 FA-1
    `default_portfolio_id(tenant_id)`로 암묵 해석돼 이전과 동일하게 동작해야
    한다 — 이 인자를 옵션으로 만든 이유(레포지토리 시그니처를 깨지 않고
    FA-0b를 적용하기 위함)의 회귀 방지."""
    from src.foundation.entities.domain.defaults import default_portfolio_id

    tenant_id = await create_test_tenant(pool)
    mandate = await repo.get_or_create_mandate(tenant_id, tenant_id)

    assert mandate.portfolio_id == default_portfolio_id(tenant_id)
    fetched = await repo.get_mandate(tenant_id)
    assert fetched is not None and fetched.id == mandate.id
