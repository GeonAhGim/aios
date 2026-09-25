"""PostgresMembershipRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-27.
DoD(task-1076): 조회·부여·상태변경이 동작하고, 활성 멤버십 partial UNIQUE
위반과 교차 테넌트 열람/변경 차단이 실DB negative test로 단언된다.

DEEPEN(task-3168)이 지적한 공백(수치 성능 단언 없음, 게이트 적색 재현
없음, D3 동시성/적대적 증명 없음)을
`test_get_active_membership_p95_under_borrowed_single_roundtrip_budget`,
`test_get_active_membership_budget_gate_fails_on_injected_regression`,
`test_concurrent_insert_membership_requests_leave_exactly_one_winner`,
`test_concurrent_legit_and_cross_tenant_state_transitions_leave_only_legit_winner`
로 메운다. replay_verify(scripts/replay_verify.py)는 이 저장소가 이벤트
소싱이 아니라 조건부 UPDATE/UNIQUE 제약만 쓰므로 N/A(이벤트 로그 없음).
"""

from __future__ import annotations

import asyncio
import math
import os
import time

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.domain.models import MembershipRole, MembershipState, TenantKind
from tests.integration.conftest import create_test_user


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


async def test_insert_tenant_and_get_personal_tenant(pool, repo):
    user_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        created = await repo.insert_tenant(conn, tenant_id=user_id, kind=TenantKind.PERSONAL)
        fetched = await repo.get_personal_tenant(conn, user_id)

    assert created.id == user_id
    assert fetched is not None
    assert fetched.id == user_id
    assert fetched.kind == TenantKind.PERSONAL


async def test_insert_membership_and_get_active_membership(pool, repo):
    user_id = await create_test_user(pool)
    membership = await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn:
        active = await repo.get_active_membership(conn, user_id, user_id)

    assert active is not None
    assert active.id == membership.id
    assert active.role == MembershipRole.OWNER
    assert active.state == MembershipState.ACTIVE
    assert active.revision == 1


async def test_insert_membership_duplicate_active_raises_concurrency_conflict(pool, repo):
    """DoD negative: 같은 tenant/subject에 ACTIVE 멤버십이 이미 있으면
    `uq_tenant_membership_active` 부분 UNIQUE 위반 -> ConcurrencyConflictError."""
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


async def test_list_memberships_for_subject_returns_all_tenants(pool, repo):
    user_id = await create_test_user(pool)
    other_tenant_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=other_tenant_id, kind=TenantKind.PERSONAL)
        await repo.insert_membership(
            conn,
            tenant_id=other_tenant_id,
            subject_id=user_id,
            role=MembershipRole.MEMBER,
            created_by=other_tenant_id,
        )
        memberships = await repo.list_memberships_for_subject(conn, user_id)

    assert {m.tenant_id for m in memberships} == {user_id, other_tenant_id}


async def test_count_active_owners(pool, repo):
    user_id = await create_test_user(pool)
    other_tenant_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn, conn.transaction():
        owned = await repo.count_active_owners(conn, user_id)
        empty = await repo.count_active_owners(conn, other_tenant_id)

    assert owned == 1
    assert empty == 0


async def test_update_conditional_membership_state_transitions_and_bumps_revision(pool, repo):
    user_id = await create_test_user(pool)
    membership = await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    async with pool.acquire() as conn:
        updated = await repo.update_conditional_membership_state(
            conn,
            membership.id,
            user_id,
            expected_state=MembershipState.ACTIVE,
            expected_revision=1,
            new_state=MembershipState.SUSPENDED,
        )

    assert updated.state == MembershipState.SUSPENDED
    assert updated.revision == 2


async def test_update_conditional_membership_state_stale_revision_raises_concurrency_conflict(
    pool, repo
):
    """DoD negative: 동시 경합 — 이미 revision이 전진했는데 옛 값으로
    전이를 시도하면 ConcurrencyConflictError."""
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


async def test_update_conditional_membership_state_wrong_tenant_raises_concurrency_conflict(
    pool, repo
):
    """DoD negative(교차 테넌트 변경 차단, LA-22 선례) — 공격자가 다른
    tenant_id로 피해자 소유 membership_id를 전이 시도하면, "존재하지 않음"
    과 동형으로 ConcurrencyConflictError만 던지고 행은 변경되지 않는다."""
    owner_id = await create_test_user(pool)
    attacker_tenant_id = await create_test_user(pool)
    membership = await _seed_owner(repo, pool, tenant_id=owner_id, subject_id=owner_id)

    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=attacker_tenant_id, kind=TenantKind.PERSONAL)
        with pytest.raises(ConcurrencyConflictError):
            await repo.update_conditional_membership_state(
                conn,
                membership.id,
                attacker_tenant_id,
                expected_state=MembershipState.ACTIVE,
                expected_revision=1,
                new_state=MembershipState.REVOKED,
            )
        unchanged = await repo.get_active_membership(conn, owner_id, owner_id)

    assert unchanged is not None
    assert unchanged.revision == 1
    assert unchanged.state == MembershipState.ACTIVE


async def test_get_active_membership_wrong_tenant_returns_none(pool, repo):
    """DoD negative(교차 테넌트 열람 차단) — 피해자의 subject_id를 알아도
    공격자 자신의 tenant_id로는 "404 동형" None만 돌아온다."""
    owner_id = await create_test_user(pool)
    attacker_tenant_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=owner_id, subject_id=owner_id)

    async with pool.acquire() as conn:
        leaked = await repo.get_active_membership(conn, attacker_tenant_id, owner_id)

    assert leaked is None


async def _get_active_membership_p95_ms(pool, repo, tenant_id, subject_id, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        async with pool.acquire() as conn:
            start = time.perf_counter()
            await repo.get_active_membership(conn, tenant_id, subject_id)
            durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[math.ceil(0.95 * len(durations_ms)) - 1]


async def test_get_active_membership_p95_under_borrowed_single_roundtrip_budget(pool, repo):
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 멤버십 조회
    전용 항목은 없다 — `get_active_membership`은 단일 SELECT(단일 실DB
    왕복)라는 점에서 가장 가까운 유사 항목("주문 제출→ACK p95 50ms(paper)")을
    자체 예산으로 차용한다(task-3162 PLT-23 DEEPEN·rotate_refresh와 동일
    차용 근거). get_active_membership 30회 반복 p95를 그 예산 내로
    단언한다."""
    user_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)

    p95_ms = await _get_active_membership_p95_ms(pool, repo, user_id, user_id, n=30)

    assert p95_ms < 50.0


async def test_get_active_membership_budget_gate_fails_on_injected_regression(
    pool, repo, monkeypatch
):
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `get_active_membership`이 쓰는 `asyncpg.Connection.fetchrow`에 60ms
    인위 지연을 주입해, 같은 측정 로직이 실제로 AssertionError를 내는지
    본다(tautology가 아님을 증명)."""
    user_id = await create_test_user(pool)
    await _seed_owner(repo, pool, tenant_id=user_id, subject_id=user_id)
    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _get_active_membership_p95_ms(pool, repo, user_id, user_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < 50.0


async def test_concurrent_insert_membership_requests_leave_exactly_one_winner(pool, repo):
    """D3 동시성 증거 — 같은 tenant/subject에 대해 서로 다른 워커 6개가
    `insert_membership`을 동시에 시도하면, `uq_tenant_membership_active`
    부분 UNIQUE가 명시적 락 없이도 fail-closed로 동작해 정확히 하나만
    성공하고 나머지 5개는 전부 `ConcurrencyConflictError`여야 한다(1076
    DoD의 순차 negative test
    `test_insert_membership_duplicate_active_raises_concurrency_conflict`를
    실제 동시 경합으로 승격, FA-2 D3 다중워커 패턴과 동일 근거 — c9e8a7e8).
    워커 수는 `pool` 픽스처의 `max_size=16`보다 낮게 고정한다."""
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=user_id, kind=TenantKind.PERSONAL)

    async def _attempt():
        async with pool.acquire() as conn:
            try:
                return await repo.insert_membership(
                    conn,
                    tenant_id=user_id,
                    subject_id=user_id,
                    role=MembershipRole.OWNER,
                    created_by=user_id,
                )
            except ConcurrencyConflictError:
                return None

    results = await asyncio.gather(*(_attempt() for _ in range(6)))
    winners = [r for r in results if r is not None]

    assert len(winners) == 1

    async with pool.acquire() as conn:
        active = await repo.get_active_membership(conn, user_id, user_id)
    assert active is not None
    assert active.id == winners[0].id


async def test_concurrent_legit_and_cross_tenant_state_transitions_leave_only_legit_winner(
    pool, repo
):
    """D3 적대적 증거(INVARIANTS I-10 "구현됨 != 작동함" 배선 증명) —
    피해자 소유 membership에 대해 정당한 tenant_id로의 전이 1건과 공격자
    tenant_id 5개로부터의 전이 시도를 동시에 쏜다. 순차 negative test
    (`test_update_conditional_membership_state_wrong_tenant_raises_...`)는
    "경합이 없을 때" tenant_id 불일치가 차단됨만 보인다 — 이 테스트는 조건부
    UPDATE의 WHERE 절(tenant_id 포함)이 실제 동시 경합 중에도 락 없이
    fail-closed로 유지되어, 공격자 시도는 5건 전부 `ConcurrencyConflictError`
    이고 정당한 전이만 성공함을 실DB로 증명한다."""
    owner_id = await create_test_user(pool)
    membership = await _seed_owner(repo, pool, tenant_id=owner_id, subject_id=owner_id)

    attacker_tenant_ids = [await create_test_user(pool) for _ in range(5)]
    async with pool.acquire() as conn:
        for attacker_tenant_id in attacker_tenant_ids:
            await repo.insert_tenant(conn, tenant_id=attacker_tenant_id, kind=TenantKind.PERSONAL)

    async def _attempt(tenant_id):
        async with pool.acquire() as conn:
            try:
                return await repo.update_conditional_membership_state(
                    conn,
                    membership.id,
                    tenant_id,
                    expected_state=MembershipState.ACTIVE,
                    expected_revision=1,
                    new_state=MembershipState.SUSPENDED,
                )
            except ConcurrencyConflictError:
                return None

    tenant_ids = [owner_id, *attacker_tenant_ids]
    results = await asyncio.gather(*(_attempt(tenant_id) for tenant_id in tenant_ids))
    winners = [r for r in results if r is not None]

    assert len(winners) == 1
    assert winners[0].tenant_id == owner_id
    assert winners[0].state == MembershipState.SUSPENDED

    async with pool.acquire() as conn:
        reread = await repo.get_active_membership(conn, owner_id, owner_id)
    assert reread is None  # SUSPENDED로 실제 전이됐으므로 ACTIVE 조회에서 사라진다
