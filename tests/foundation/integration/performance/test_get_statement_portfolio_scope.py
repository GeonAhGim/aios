"""FA-6 — `application/get_statement.py`의 선택적 `portfolio_id` 스코프.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-6
(§9 표 120행, "기존 단일계좌 응답 무변경").

`performance_statement`에는 아직 portfolio_id 컬럼이 없다(get_statement.py
모듈 docstring 참조) — 이 테스트는 (1) `portfolio_id`를 생략하면 이전 리프와
동일하게 동작하고(회귀), (2) tenant의 FA-1 기본 포트폴리오를 주면 통과하며,
(3) 존재하지 않거나 다른 tenant 소유인 portfolio_id는 fail-closed로 거부됨을
실제 DB(TEST_DATABASE_URL)로 확인한다."""
from __future__ import annotations

import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.entities.application.resolve_context import EntityContextResolutionError
from src.foundation.entities.domain.defaults import build_default_hierarchy, default_portfolio_id
from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.application.get_statement import get_statement, list_statements
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities.conftest import build_hierarchy

_NOW = datetime.now(timezone.utc)


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def perf_repo(pool: asyncpg.Pool) -> PostgresPerformanceRepository:
    return PostgresPerformanceRepository(pool)


@pytest.fixture
def entity_repo(pool: asyncpg.Pool) -> PostgresEntityRepository:
    return PostgresEntityRepository(pool)


def _breakdown() -> ComponentBreakdown:
    return ComponentBreakdown(
        gross_pnl=Decimal("120"), fees=Decimal("10"), slippage=Decimal("5"),
        funding=Decimal("2"), fx=Decimal("0"), cashflows_net=Decimal("1000"),
        estimated_tax=Decimal("3"), net_pnl=Decimal("100"),
    )


async def _statement(tenant_id, **overrides) -> PerformanceStatement:
    defaults = dict(
        id=uuid4(), tenant_id=tenant_id, scope="PAPER", scope_ref=str(tenant_id),
        period_start=_NOW, period_end=_NOW, as_of=_NOW,
        methodology_version=DEFAULT_METHODOLOGY.version,
        methodology_hash=DEFAULT_METHODOLOGY.methodology_hash,
        input_refs=("snapshot:abc",), components=_breakdown(),
        returns=(
            ReturnFigure(
                value_pct=Decimal("0.21"), basis="NET", method="TWR",
                period_start=_NOW, period_end=_NOW, annualized=False, periods_per_year=None,
            ),
        ),
        risk={"vol_pct": Decimal("0.05"), "sharpe": None}, benchmark=None, benchmark_ref=None,
        state=StatementState.ESTIMATED, revision_no=1, prior_statement_id=None,
        identity_ok=True, identity_residual=Decimal("0"), limitations=(),
        evidence_refs=("audit:1",),
    )
    defaults.update(overrides)
    return PerformanceStatement(**defaults)


async def _seed_default_hierarchy(
    pool: asyncpg.Pool, repo: PostgresEntityRepository, tenant_id
) -> None:
    """FA-1 결정론(UUIDv5) 기본 계층을 실제로 영속화한다 — `build_hierarchy`
    (entities conftest)는 무작위 id를 쓰므로, `default_portfolio_id(tenant_id)`와
    일치하는 포트폴리오가 필요한 이 테스트에는 맞지 않는다."""
    hierarchy = build_default_hierarchy(
        user_id=tenant_id,
        tenant_id=tenant_id,
        base_currency=Currency.USDT,
        jurisdiction="KR",
        region_tag="kr-seoul",
        venue_account_ref="venue-acct-1",
        inception=date(2026, 1, 1),
    )
    await repo.create_legal_entity(hierarchy.legal_entity)
    await repo.create_fund(hierarchy.fund)
    await repo.create_portfolio(hierarchy.portfolio)
    await repo.create_sub_account(hierarchy.sub_account)


async def test_get_and_list_statements_without_portfolio_id_is_unchanged_regression(
    pool, perf_repo
):
    tenant_id = await create_test_tenant(pool)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))

    got = await get_statement(perf_repo, tenant_id=tenant_id, statement_id=inserted.id)
    listed = await list_statements(perf_repo, tenant_id=tenant_id)

    assert got.id == inserted.id
    assert [s.id for s in listed] == [inserted.id]


async def test_get_and_list_statements_accept_tenant_default_portfolio_id(
    pool, perf_repo, entity_repo
):
    tenant_id = await create_test_tenant(pool)
    await _seed_default_hierarchy(pool, entity_repo, tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))
    portfolio_id = default_portfolio_id(tenant_id)

    got = await get_statement(
        perf_repo,
        tenant_id=tenant_id,
        statement_id=inserted.id,
        portfolio_id=portfolio_id,
        entities=entity_repo,
    )
    listed = await list_statements(
        perf_repo, tenant_id=tenant_id, portfolio_id=portfolio_id, entities=entity_repo
    )

    assert got.id == inserted.id
    assert [s.id for s in listed] == [inserted.id]


async def test_get_statement_rejects_other_portfolio_id_fail_closed(pool, perf_repo, entity_repo):
    """negative — statement가 귀속되지 않은(오늘의 유일한 귀속처가 아닌)
    portfolio_id를 주면 tenant 전체를 돌려주는 대신 거부한다."""
    tenant_id = await create_test_tenant(pool)
    hierarchy = await build_hierarchy(pool, entity_repo, tenant_id=tenant_id)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)
    inserted = await perf_repo.insert_statement(await _statement(tenant_id))

    with pytest.raises(EntityContextResolutionError):
        await get_statement(
            perf_repo,
            tenant_id=tenant_id,
            statement_id=inserted.id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            entities=entity_repo,
        )


async def test_get_statement_rejects_cross_tenant_portfolio_id_fail_closed(
    pool, perf_repo, entity_repo
):
    """negative — 다른 tenant가 실제로 소유한 portfolio_id를 주면(값 자체는
    유효) 전체 반환 대신 거부한다(교차 테넌트 유출 방지)."""
    victim_tenant = await create_test_tenant(pool)
    victim_hierarchy = await build_hierarchy(pool, entity_repo, tenant_id=victim_tenant)
    await perf_repo.insert_methodology(DEFAULT_METHODOLOGY)

    attacker_tenant = await create_test_tenant(pool)
    inserted = await perf_repo.insert_statement(await _statement(attacker_tenant))

    with pytest.raises(EntityContextResolutionError):
        await get_statement(
            perf_repo,
            tenant_id=attacker_tenant,
            statement_id=inserted.id,
            portfolio_id=victim_hierarchy.portfolio.portfolio_id,
            entities=entity_repo,
        )
