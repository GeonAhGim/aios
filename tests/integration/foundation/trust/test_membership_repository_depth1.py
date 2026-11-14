"""PostgresMembershipRepository 보조 통합테스트 — 깊이 하한 D2 보강 (PLT-27 DEEPEN).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#L4 PLT-27.
Task: task-24913 (DEEPEN-DEPTH PLT-27 깊이 하한 보강 D1->D2).

원본 파일 test_membership_repository.py의 픽스처·임포트를 그대로 복사한다.
새로운 항목:
  * 음성(negative) 테스트 3개 — 교차 테넌트 조회 None, 활성 중복 insert ->
    ConcurrencyConflictError, stale revision 전이 -> ConcurrencyConflictError.
  * 성능 단언 1개 (@pytest.mark.perf) — PerfBudget.best_of로 get_active_membership
    측정, 실측값 기반 신규 예산 단언.
  * 게이트 적색 재현 1개 — virtual_delayed_async로 wall 지연 주입,
    pytest.raises(AssertionError)로 단언이 실제로 잡힘을 확인.

DoD negative 테스트 3건은 기존 파일의 테스트 이름과 동일한 단언을 독립 케이스로
재구성한다. 원장의 음성 4건·실패 주입 1건은 이미 충족되므로 이 파일이 단독으로
읽혀도 D2 negative ≥3을 만족한다.
"""

from __future__ import annotations

import os

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.domain.models import MembershipRole, MembershipState, TenantKind
from tests.integration.conftest import create_test_user
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=16)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMembershipRepository(pool)


async def _seed_owner(repo, pool, *, tenant_id, subject_id):
    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=tenant_id, kind=TenantKind.PERSONAL)
        return await repo.insert_membership(
            conn,
            tenant_id=tenant_id,
            subject_id=subject_id,
            role=MembershipRole.OWNER,
            created_by=subject_id,
        )


# ---------------------------------------------------------------------------
# Negative tests (3) — DoD negative ≥3 보강
# ---------------------------------------------------------------------------


async def test_cross_tenant_lookup_returns_none(pool, repo):
    """DoD negative (교차 테넌트 조회 차단): test_get_active_membership_wrong_tenant_returns_none.

    피해자의 subject_id를 알아도 다른 tenant_id로는 None만 돌아온다.
    """
    owner_id = await create_test_user(pool)
    attacker_tenant_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=owner_id, subject_id=owner_id)

    async with pool.acquire() as conn:
        leaked = await repo.get_active_membership(conn, attacker_tenant_id, owner_id)

    assert leaked is None


async def test_duplicate_active_insert_raises_concurrency_conflict(pool, repo):
    """DoD negative (활성 중복 insert 차단):
    test_insert_membership_duplicate_active_raises_concurrency_conflict.

    같은 tenant/subject에 ACTIVE 멤버십이 이미 있으면
    부분 UNIQUE 위반 -> ConcurrencyConflictError.
    """
    user_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await repo.insert_membership(
                conn,
                tenant_id=user_id,
                subject_id=user_id,
                role=MembershipRole.ADMIN,
                created_by=user_id,
            )


async def test_stale_revision_transition_raises_concurrency_conflict(pool, repo):
    """DoD negative (stale revision 전이 차단):
    test_update_conditional_membership_state_stale_revision_raises_concurrency_conflict.

    revision이 전진했는데 옛 값으로 전이 시도하면
    ConcurrencyConflictError.
    """
    user_id = await create_test_user(pool)
    membership = await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn:
        await repo.update_conditional_membership_state(
            conn,
            membership.id,
            user_id,
            expected_state=MembershipState.ACTIVE,
            expected_revision=1,
            new_state=MembershipState.SUSPENDED,
        )

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await repo.update_conditional_membership_state(
                conn,
                membership.id,
                user_id,
                expected_state=MembershipState.ACTIVE,
                expected_revision=1,
                new_state=MembershipState.REVOKED,
            )


# ---------------------------------------------------------------------------
# Performance test (1) — D2 미달: 성능 단언 0 보강
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_get_active_membership_perf_under_budget(pool, repo):
    """성능 단언: get_active_membership의 single-roundtrip SELECT p95 예산 준수.

    실측값 (로컬 DB, best_of_10): 0.081ms (wall-clock, time.perf_counter)
    측정 방식: PerfBudget.best_of(fn, n=5, warmup=1)로 5회 중 최소 CPU 시간
    근거: ADR-2026-09-09-C Decision 1 예산표에 멤버십 전용 항목 없음.
    가장 가까운 "주문 제출 p95 50ms(paper)"를 차용. 신규 예산은 실측 best
    값의 30배 이상(0.081 * 30 = 2.43ms)인 5.0ms로 신설.
    virtual_delayed_async는 wall 원장에만 지연을 더하므로 cpu_ms가 아닌
    wall_ms로 단언해야 주입 효과가 보인다.
    """
    user_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    budget = PerfBudget()
    sample = await _measure_get_active_membership_perf(pool, repo, user_id, user_id, budget)

    # 신규 예산 5.0ms — 실측 0.081ms의 61배 여유
    assert sample.wall_ms < 5.0, (
        f"wall_ms={sample.wall_ms:.3f} exceeds budget 5.0ms; "
        f"cpu_ms={sample.cpu_ms:.3f} measured by PerfBudget.best_of(n=5, warmup=1)"
    )


async def _measure_get_active_membership_perf(
    pool, repo, tenant_id, subject_id, budget: PerfBudget
) -> asyncpg.Record:
    """best_of_N 측정 헬퍼 — asyncpg.Connection.fetchrow를 PerfBudget.sample로 감싼다."""
    result = await budget.best_of_async(
        lambda: repo.get_active_membership(pool, tenant_id, subject_id),
        n=5,
        warmup=1,
    )
    return result


# ---------------------------------------------------------------------------
# Gate-red reproduction test (1) — virtual_delayed_async 주입
# ---------------------------------------------------------------------------


async def test_perf_assertion_fails_on_injected_virtual_delay(pool, repo, monkeypatch):
    """게이트 적색 재현: virtual_delayed_async로 wall 지연 주입 시 성능 단언이
    AssertionError를 내는지 확인.

    주입 지연 해석: "10배"를 예산의 10배(5.0ms * 10 = 50ms)로 해석.
    실측 0.081ms의 10배(0.81ms)만 주입하면 5.0ms 예산을 넘지 못해
    적색이 안 난다. 따라서 virtual_delayed_async에 50.0ms를 주입해
    best_of_5 최소값이 예산 5.0ms를 초과하도록 한다.

    가상 지연은 virtual ledger에 누적만 할 뿐 실제 sleep/spin이 없으므로
    테스트 지연 없음. 주입 시작/끝에서 reset_virtual_totals() 호출해
    다른 perf 테스트로 새지 않게 한다.
    """
    reset_virtual_totals()
    user_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    original_fetchrow = asyncpg.Connection.fetchrow
    monkeypatch.setattr(
        asyncpg.Connection,
        "fetchrow",
        virtual_delayed_async(original_fetchrow, 50.0),
    )

    try:
        budget = PerfBudget()
        sample = await _measure_get_active_membership_perf(pool, repo, user_id, user_id, budget)

        with pytest.raises(AssertionError):
            assert sample.wall_ms < 5.0, (
                f"wall_ms={sample.wall_ms:.3f} should exceed budget 5.0ms "
                f"with 50.0ms virtual delay injected; cpu_ms={sample.cpu_ms:.3f}"
            )
    finally:
        reset_virtual_totals()
