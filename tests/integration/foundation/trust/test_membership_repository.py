"""PostgresMembershipRepository 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-27.
DoD(task-1076): 조회·부여·상태변경이 동작하고, 활성 멤버십 partial UNIQUE
위반과 교차 테넌트 열람/변경 차단이 실DB negative test로 단언된다.
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
