"""PostgresPerformanceRepository 통합테스트 — 실제 dev DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L47 DoD:
"저장/조회, REVOKE 검증")."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    AttributionSlice,
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)
from tests.integration.conftest import create_test_user

_NOW = datetime.now(timezone.utc)


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
    return PostgresPerformanceRepository(pool)


def _breakdown() -> ComponentBreakdown:
    return ComponentBreakdown(
        gross_pnl=Decimal("120"),
        fees=Decimal("10"),
        slippage=Decimal("5"),
        funding=Decimal("2"),
        fx=Decimal("0"),
        cashflows_net=Decimal("1000"),
        estimated_tax=Decimal("3"),
        net_pnl=Decimal("100"),
    )


async def _statement(tenant_id, **overrides) -> PerformanceStatement:
    defaults = dict(
        id=uuid4(),
        tenant_id=tenant_id,
        scope="PAPER",
        scope_ref="deployment-1",
        period_start=_NOW,
        period_end=_NOW,
        as_of=_NOW,
        methodology_version=DEFAULT_METHODOLOGY.version,
        methodology_hash=DEFAULT_METHODOLOGY.methodology_hash,
        input_refs=("snapshot:abc",),
        components=_breakdown(),
        returns=(
            ReturnFigure(
                value_pct=Decimal("0.21"),
                basis="NET",
                method="TWR",
                period_start=_NOW,
                period_end=_NOW,
                annualized=False,
                periods_per_year=None,
            ),
        ),
        risk={"vol_pct": Decimal("0.05"), "sharpe": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.ESTIMATED,
        revision_no=1,
        prior_statement_id=None,
        identity_ok=True,
        identity_residual=Decimal("0"),
        limitations=(),
        evidence_refs=("audit:1",),
    )
    defaults.update(overrides)
    return PerformanceStatement(**defaults)


async def test_insert_and_get_statement_round_trips_decimal_precision(pool, repo):
    tenant_id = await create_test_user(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await _statement(tenant_id)

    inserted = await repo.insert_statement(statement)
    fetched = await repo.get_statement(inserted.id)

    assert fetched is not None
    assert fetched.components.net_pnl == Decimal("100")
    assert fetched.components.gross_pnl == Decimal("120")
    assert fetched.methodology_hash == DEFAULT_METHODOLOGY.methodology_hash
    assert fetched.returns[0].value_pct == Decimal("0.21")
    assert fetched.risk["sharpe"] is None
    assert fetched.evidence_refs == ("audit:1",)


async def test_list_statements_scoped_to_tenant(pool, repo):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_a))

    view_b = await repo.list_statements(tenant_id=tenant_b)

    assert view_b == ()


async def test_get_latest_statement_returns_highest_revision(pool, repo):
    tenant_id = await create_test_user(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    first_inserted = await repo.insert_statement(await _statement(tenant_id, revision_no=1))
    second = await _statement(
        tenant_id,
        revision_no=2,
        prior_statement_id=first_inserted.id,
        state=StatementState.CORRECTED,
    )
    await repo.insert_statement(second)

    latest = await repo.get_latest_statement(
        tenant_id=tenant_id,
        scope="PAPER",
        scope_ref="deployment-1",
        period_start=_NOW,
        period_end=_NOW,
        methodology_version=DEFAULT_METHODOLOGY.version,
    )

    assert latest is not None
    assert latest.revision_no == 2
    assert latest.state == StatementState.CORRECTED


async def test_public_role_has_no_update_or_delete_grant_on_statement(pool):
    """WORM — 마이그레이션의 `REVOKE UPDATE, DELETE ... FROM PUBLIC`가 실제로
    적용됐는지 카탈로그로 확인한다. 이 테스트가 접속하는 DB 사용자는
    테이블 소유자라 REVOKE...FROM PUBLIC의 영향을 받지 않는다(Postgres의
    소유자 특권은 GRANT/REVOKE 경로를 거치지 않는다 — 다른 WORM 테이블
    (foundation_audit_event 등)도 같은 이유로 실제 접속 차단을 테스트하지
    않는다) — 그래서 소유자가 아니라 PUBLIC 자체의 권한을 직접 확인한다."""
    async with pool.acquire() as conn:
        grants = await conn.fetch(
            "SELECT privilege_type FROM information_schema.table_privileges "
            "WHERE table_name = 'performance_statement' AND grantee = 'PUBLIC'"
        )
    granted_privileges = {row["privilege_type"] for row in grants}
    assert "UPDATE" not in granted_privileges
    assert "DELETE" not in granted_privileges


async def test_attribution_slices_persist_and_list_by_statement(pool, repo):
    tenant_id = await create_test_user(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await repo.insert_statement(await _statement(tenant_id))

    await repo.insert_attribution(
        AttributionSlice(
            statement_id=statement.id,
            dimension="strategy",
            key="rsi-sma",
            contribution=Decimal("0.6"),
            confidence=Decimal("0.9"),
            limitation=None,
        )
    )
    await repo.insert_attribution(
        AttributionSlice(
            statement_id=statement.id,
            dimension="strategy",
            key="macd",
            contribution=Decimal("0.4"),
            confidence=None,
            limitation="표본 부족",
        )
    )

    slices = await repo.list_attribution(statement.id)

    assert len(slices) == 2
    assert {s.key for s in slices} == {"rsi-sma", "macd"}


async def test_insert_methodology_is_idempotent_on_conflict(pool, repo):
    first = await repo.insert_methodology(DEFAULT_METHODOLOGY)
    second = await repo.insert_methodology(DEFAULT_METHODOLOGY)

    assert first.methodology_hash == second.methodology_hash

    fetched = await repo.get_methodology(DEFAULT_METHODOLOGY.version)
    assert fetched is not None
    assert fetched.risk_free_rate_pct == DEFAULT_METHODOLOGY.risk_free_rate_pct


async def test_get_methodology_returns_none_when_missing(repo):
    assert await repo.get_methodology("nonexistent-version") is None
