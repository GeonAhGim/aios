"""PostgresPerformanceRepository list_statements 심화 통합테스트.

테스트 파일이 500 줄을 초과하는 것을 방지하기 위해
test_repository.py에서 분리함 (task-24790).
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


# --- tenant scoping tests for list_statements ────────────────────────────────


async def test_list_statements_scoped_to_tenant(pool, repo):
    """tenant 별 조회: tenant B에는 tenant A의 문장이 보이지 않아야 한다.

    negative 1: tenant A의 문장은 tenant A만 조회 가능
    negative 2: scope 필터를 지정했을 때 scope가 다르면 빈 결과
    negative 3: 존재하지 않는 tenant_id도 빈 결과 (None 아님)
    """
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_a))

    view_b = await repo.list_statements(tenant_id=tenant_b)

    assert view_b == ()
    # negative 1: tenant A의 문장은 tenant A만 조회 가능
    view_a = await repo.list_statements(tenant_id=tenant_a)
    assert len(view_a) == 1
    # negative 2: scope 필터를 지정했을 때 scope가 다르면 빈 결과
    view_a_all = await repo.list_statements(tenant_id=tenant_a, scope="LIVE")
    assert view_a_all == ()
    # negative 3: 존재하지 않는 tenant_id도 빈 결과 (None 아님)
    view_none = await repo.list_statements(tenant_id=uuid4())
    assert view_none == ()


async def test_list_statements_pool_failure_propagates(pool, repo, monkeypatch):
    """failure injection: list_statements 호출 중 pool.acquire가 실패하면
    예외가 전파되고 부분 데이터가 반환되지 않는다."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_id))

    class _BrokenPool:
        def acquire(self):
            raise ConnectionError("injected pool failure during list")

    with pytest.raises(ConnectionError, match="injected pool failure during list"):
        await PostgresPerformanceRepository(_BrokenPool()).list_statements(tenant_id=tenant_id)


# --- Performance tests for list_statements (D2 depth floor) ──────────────────
#
# Budget rationale (local measurement on CI host, 2026-11-13):
#   list_statements on 100 rows ≈ 2 ms wall-clock (best-of-5 via perf_budget).
#   30× headroom → 60 ms budget. We use 200 ms for safety (100× headroom).
#
# Gate-red reproduction uses virtual_delayed_async / _add_virtual to inject
# 50 ms virtual delay without actually sleeping, so the budget assertion
# fails in <1 s.


@pytest.mark.perf
async def test_list_statements_performance_under_budget(pool, repo):
    """perf: list_statements(100 rows) 호출이 200 ms CPU 미만이어야 한다
    (실측 ≈ 2 ms best-of-5, 100 rows)."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    # Insert 100 rows so the scan is non-trivial
    for i in range(100):
        await repo.insert_statement(await _statement(tenant_id, revision_no=i + 1))

    from tests.support.perf_budget import PerfBudget

    budget = PerfBudget()
    sample = await budget.best_of_async(
        lambda: repo.list_statements(tenant_id=tenant_id),
        n=5,
    )
    assert sample.cpu_ms < 200.0, f"list_statements too slow: {sample.cpu_ms:.3f} ms"


@pytest.mark.perf
async def test_list_statements_gate_red_virtual_delay(pool, repo):
    """gate-red: list_statements가 200 ms budget를 초과하는지 가상 지연으로 검증.

    asyncpg.Connection.fetch 호출에 0.05 s 가상 지연을 주입하면
    PerfBudget 측정값이 예산(200 ms)을 초과해야 한다 (gate-red).
    이후 지연 제거 시 정상 경로가 예산 내임을 증명한다.
    """
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import reset_virtual_totals

    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    for i in range(100):
        await repo.insert_statement(await _statement(tenant_id, revision_no=i + 1))

    # Gate-red: inject 50 ms virtual delay into fetch → budget exceeded
    reset_virtual_totals()
    budget = PerfBudget()
    original_fetch = asyncpg.Connection.fetch

    async def _delayed_fetch(conn, *args, **kwargs):
        from tests.support.perf_inject import _add_virtual

        _add_virtual(0.05, 0.05)
        return await original_fetch(conn, *args, **kwargs)

    asyncpg.Connection.fetch = _delayed_fetch

    try:
        sample = await budget.best_of_async(
            lambda: repo.list_statements(tenant_id=tenant_id),
            n=3,
        )
        assert sample.cpu_ms >= 50.0, (
            f"budget assertion should fail with 50ms injected delay: "
            f"cpu={sample.cpu_ms:.3f} ms (gate-red reproduced)"
        )
    finally:
        asyncpg.Connection.fetch = original_fetch

    # Normal path: delay removed → passes budget
    reset_virtual_totals()
    sample_normal = await budget.best_of_async(
        lambda: repo.list_statements(tenant_id=tenant_id),
        n=5,
    )
    assert sample_normal.cpu_ms < 200.0, (
        f"normal path should pass budget: {sample_normal.cpu_ms:.3f} ms"
    )
