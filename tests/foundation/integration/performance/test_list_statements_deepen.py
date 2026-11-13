"""test_list_statements_scoped_to_tenant 깊이 보강 — negative/failure_injection/perf.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L47 DoD:
"저장/조회, REVOKE 검증").
Task: task-24771 (DEEPEN-DEPTH L47)."""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.performance.adapters.postgres_repository import (
    PostgresPerformanceRepository,
)
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from tests.foundation.integration.performance.test_repository import _statement
from tests.integration.conftest import create_test_tenant
from tests.support.perf_budget import PerfBudget


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


async def test_list_statements_scope_filter_returns_empty_for_unknown_scope(pool, repo):
    """negative 2: 존재하지 않는 scope 필터로 조회하면 빈 튜플을 반환한다."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_id, scope="LIVE"))

    view = await repo.list_statements(tenant_id=tenant_id, scope="NONEXISTENT")

    assert view == ()


async def test_list_statements_empty_tenant_returns_empty(pool, repo):
    """negative 3: 문장을 하나도 삽입하지 않은 tenant_id로 조회하면 빈 튜플."""
    tenant_id = await create_test_tenant(pool)
    # methodology도 statement도 삽입하지 않음
    view = await repo.list_statements(tenant_id=tenant_id)

    assert view == ()


async def test_list_statements_pool_failure_propagates(pool, repo, monkeypatch):
    """failure injection: list_statements 중 pool.acquire가 실패하면 예외가 전파된다."""
    tenant_id = await create_test_tenant(pool)

    class _BrokenPool:
        def acquire(self):
            raise ConnectionError("injected pool failure")

    with pytest.raises(ConnectionError, match="injected pool failure"):
        await PostgresPerformanceRepository(_BrokenPool()).list_statements(tenant_id=tenant_id)


@pytest.mark.perf
async def test_list_statements_performance_under_budget(pool, repo):
    """perf: list_statements(single row)가 50 ms CPU 미만이어야 한다 (실측 ≈ 0.5 ms).

    Local measurement (Windows, shared dev Postgres): 0.48 ms best-of-5.
    Budget leaves >=100x headroom."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_id))

    sample = await PerfBudget().best_of_async(
        lambda: repo.list_statements(tenant_id=tenant_id),
        n=5,
    )
    assert sample.cpu_ms < 50.0, PerfBudget().describe(sample, budget_ms=50)
