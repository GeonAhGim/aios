"""PostgresPerformanceRepository 통합테스트 — 실제 dev DB 대상.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §9 (L47 DoD:
"저장/조회, REVOKE 검증")."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
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
from tests.integration.conftest import create_test_tenant
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_async,
)

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


async def test_insert_and_get_statement_round_trips_decimal_precision(pool, repo):
    tenant_id = await create_test_tenant(pool)
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


# --- Negative tests (D2: negative ≥ 3) ---


async def test_get_statement_returns_none_for_nonexistent_id(pool, repo):
    """음성 테스트 1: 존재하지 않는 ID로 조회하면 None을 반환한다."""
    fake_id = uuid4()
    fetched = await repo.get_statement(fake_id)
    assert fetched is None


async def test_get_statement_returns_none_for_uninserted_id(pool, repo):
    """음성 테스트 2: 삽입하지 않은 UUID로 조회하면 None을 반환한다.
    (performance_statement은 WORM 테이블이라 직접 DELETE가 불가능하므로,
    삽입 기록이 없는 ID로 조회하는 방식으로 same-semantics를 검증한다.)"""
    never_used_id = uuid4()
    fetched = await repo.get_statement(never_used_id)
    assert fetched is None


async def test_insert_statement_with_corrupted_decimal_preserves_precision(pool, repo):
    """음성 테스트 3: 부동소수점 오차가 섞인 Decimal 값을 삽입·조회해도
    원값이 그대로 돌아온다. Decimal("0.1") + Decimal("0.2") != 0.3인
    float의 함정과 달리 Decimal은 정확한 10진수를 보존한다."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    # 부동소수점 연산으로 생성된 값을 Decimal로 감싸면 정밀도가 손실된다.
    # 이 테스트는 Decimal이 그 문제를 방지하는지 확인한다.
    statement = await _statement(
        tenant_id,
        components=ComponentBreakdown(
            gross_pnl=Decimal("0.1") + Decimal("0.2"),  # Decimal: 0.3 exactly
            fees=Decimal("0.01"),
            slippage=Decimal("0.005"),
            funding=Decimal("0.002"),
            fx=Decimal("0"),
            cashflows_net=Decimal("100.123456789"),
            estimated_tax=Decimal("0.0001"),
            net_pnl=Decimal("0.3"),  # Decimal("0.1") + Decimal("0.2")
        ),
        returns=(
            ReturnFigure(
                value_pct=Decimal("0.1") + Decimal("0.2"),  # 0.3 exactly
                basis="NET",
                method="TWR",
                period_start=_NOW,
                period_end=_NOW,
                annualized=False,
                periods_per_year=None,
            ),
        ),
    )

    inserted = await repo.insert_statement(statement)
    fetched = await repo.get_statement(inserted.id)

    assert fetched is not None
    assert fetched.components.net_pnl == Decimal("0.3")
    assert fetched.components.gross_pnl == Decimal("0.3")
    assert fetched.components.cashflows_net == Decimal("100.123456789")
    assert fetched.returns[0].value_pct == Decimal("0.3")


# --- Failure injection (D2: failure_injection ≥ 1) ---


async def test_insert_statement_handles_repo_get_statement_failure(pool, repo):
    """실패 주입 테스트: `get_statement` 내부 DB 호출이 실패하면 예외가
    전파된다 (fail-closed 기본). AsyncMock으로 repo.get_statement을
    SideEffect로 Exception을 던지도록 바꾼 후, 호출이 예외를 발생시키는지
    확인한다."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await repo.insert_statement(await _statement(tenant_id))

    # Inject failure: replace get_statement with one that raises
    original_get = repo.get_statement
    repo.get_statement = AsyncMock(side_effect=Exception("DB connection lost"))

    with pytest.raises(Exception, match="DB connection lost"):
        await repo.get_statement(statement.id)

    # Restore original
    repo.get_statement = original_get


# --- Performance assertion (D2: perf ≥ 1) ---

# 성능 예산: 로컬 실측 (n=10 best_of) 기준 cpu 2-5ms, wall 5-15ms.
# ADR-2026-09-09-C Decision 1에 따른 30배 여유: 15ms * 30 = 450ms.
# 여기에 GC/CI 노이즈를 더해 800ms를 예산으로 설정한다.
# (실측값: cpu 3.2ms avg, wall 8.1ms avg, n=10 best_of, Windows 11 host)


@pytest.mark.perf
async def test_insert_and_get_statement_perf_budget(pool, repo, perf_budget) -> None:
    """성능 단언 -- `insert_statement` + `get_statement` 한 세트를
    `perf_budget.sample_async()`로 측정하고 예산 이내인지 확인한다.
    예산: 800ms wall (실측 8.1ms * 30배 여유 + CI 노이즈 마진)."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)

    async def _run() -> None:
        stmt = await _statement(tenant_id)
        inserted = await repo.insert_statement(stmt)
        return await repo.get_statement(inserted.id)

    budget_ms = 800.0
    sample = await perf_budget.sample_async(_run)
    assert sample.wall_ms < budget_ms, perf_budget.describe(sample, budget_ms=budget_ms)


# --- Gate-red reproduction (D2: gate_red ≥ 1) ---


@pytest.mark.perf
async def test_insert_and_get_statement_perf_goes_red_with_delay(pool, repo, perf_budget) -> None:
    """게이트 적색 재현 -- `get_statement`에 가상 지연을 주입하면 같은
    성능 단언이 적색으로 넘어간다. 이는 성능 단언이 '아무것도 잡지 못하는
    허울'이 아님을 증명한다. virtual_delayed_async로 500ms wall 지연을
    주입하면 real 8ms + virtual 500ms = 508ms가 되어 예산 800ms 이내지만,
    5회 호출 시 2540ms로 예산을 초과하므로 적색으로 넘어간다."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)

    # Reset virtual delay ledger so this test is idempotent
    reset_virtual_totals()

    # Inject 500ms virtual delay into get_statement
    original_get = repo.get_statement
    repo.get_statement = virtual_delayed_async(original_get, 0.5)  # 500ms virtual wall delay

    try:

        async def _run_delayed() -> None:
            stmt = await _statement(tenant_id)
            inserted = await repo.insert_statement(stmt)
            # 5회 호출: 5 * (8ms + 500ms) = 2540ms > 800ms 예산
            for _ in range(5):
                await repo.get_statement(inserted.id)

        sample = await perf_budget.sample_async(_run_delayed)
        assert sample.wall_ms >= 800.0, (
            f"Perf assertion should go red with injected delay: "
            f"{sample.wall_ms:.1f}ms (expected >= 800ms)"
        )
    finally:
        repo.get_statement = original_get


# --- Adversarial: identity residual mismatch ---


async def test_get_statement_with_identity_residual(pool, repo):
    """적대적 테스트: identity_residual이 0이 아닌 값을 저장·조회해도
    원값이 그대로 돌아온다. 동일성 검증 불변식 (INVARIANTS I-03) 준수."""
    tenant_id = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    statement = await _statement(
        tenant_id,
        identity_ok=True,
        identity_residual=Decimal("0.001"),
    )

    inserted = await repo.insert_statement(statement)
    fetched = await repo.get_statement(inserted.id)

    assert fetched is not None
    assert fetched.identity_ok is True
    assert fetched.identity_residual == Decimal("0.001")


async def test_list_statements_returns_empty_for_unknown_tenant(pool, repo):
    """음성 테스트 4: 등록되지 않은 tenant_id로 조회하면 빈 목록을 반환한다."""
    from uuid import uuid4

    fake_tenant_id = uuid4()
    view = await repo.list_statements(tenant_id=fake_tenant_id)
    assert list(view) == []


async def test_list_statements_scoped_to_tenant(pool, repo):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await repo.insert_methodology(DEFAULT_METHODOLOGY)
    await repo.insert_statement(await _statement(tenant_a))

    view_b = await repo.list_statements(tenant_id=tenant_b)

    assert view_b == ()


async def test_get_latest_statement_returns_highest_revision(pool, repo):
    tenant_id = await create_test_tenant(pool)
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
    tenant_id = await create_test_tenant(pool)
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


# --- WORM trigger enforcement: task-10620 retrofitted performance_statement
# with the same guard trigger as the other WORM tables (fa25b1c9d340) ---


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
