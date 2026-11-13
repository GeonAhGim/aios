"""WORM (Write-Once-Read-Many) trigger enforcement for performance_statement.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (WORM guard).
These tests were split from test_repository.py to keep LOC ≤ 500.
"""

from __future__ import annotations

from datetime import UTC, datetime
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
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
)
from tests.integration.conftest import create_test_tenant

_NOW = datetime.now(UTC)


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


async def _statement(tenant_id) -> PerformanceStatement:
    return PerformanceStatement(
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


async def test_worm_trigger_blocks_table_owner_update(pool, repo):
    """REVOKE does not bind the table owner (PostgreSQL rule) -- `pool`
    connects without `SET ROLE` and owns `performance_statement` (the
    migration ran as this account), so if UPDATE is blocked here it is the
    `BEFORE UPDATE OR DELETE` guard trigger (fa25b1c9d340, retrofitted after
    task-10542 found the gap -- this table had only `REVOKE UPDATE, DELETE
    ... FROM PUBLIC` from 6e5baa1c7a55 and no trigger), not the REVOKE,
    doing the blocking."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await repo.insert_statement(await _statement(tenant_id))

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE performance_statement SET state = 'CORRECTED' WHERE id = $1",
                statement.id,
            )


async def test_worm_trigger_blocks_table_owner_delete(pool, repo):
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await repo.insert_statement(await _statement(tenant_id))

    with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("DELETE FROM performance_statement WHERE id = $1", statement.id)
