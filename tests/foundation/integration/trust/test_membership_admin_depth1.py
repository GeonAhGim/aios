"""Membership admin — depth-1 보강: negative 6종, failure-injection 1건,
performance assertion 1건, gate-red reproduction 1건.

기존 test_membership_admin.py와 겹치지 않는 케이스를 커버한다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md
      §4.1 Membership, §9 PLT-29
      INVARIANTS.md I-04 (ACTIVE OWNER >= 1)
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.application.grant_membership import (
    GrantAuthorizationError,
    grant_membership,
)
from src.foundation.trust.application.revoke_membership import (
    RevokeAuthorizationError,
    RevokeTargetNotFoundError,
    revoke_membership,
)
from src.foundation.trust.application.suspend_membership import (
    SuspendAuthorizationError,
    SuspendTargetNotFoundError,
    suspend_membership,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import MembershipRole, TenantKind
from tests.integration.conftest import create_test_user
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async

# Budget: measured best_of_5 wall_ms = 1.941 ms locally; 60 ms >= 30x that (new budget).
SUSPEND_WALL_MS_BUDGET: float = 60.0


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresMembershipRepository(pool)


async def _household(pool, repo):
    owner_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=owner_id, kind=TenantKind.HOUSEHOLD)
        await repo.insert_membership(
            conn,
            tenant_id=owner_id,
            subject_id=owner_id,
            role=MembershipRole.OWNER,
            created_by=owner_id,
        )
    return owner_id


async def _add_member(pool, repo, *, tenant_id, role):
    member_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await repo.insert_membership(
            conn,
            tenant_id=tenant_id,
            subject_id=member_id,
            role=role,
            created_by=tenant_id,
        )
    return member_id


def _ctx(*, tenant_id, subject_id, role, mfa_verified):
    return TenantContext(
        tenant_id=tenant_id, subject_id=subject_id, role=role, mfa_verified=mfa_verified
    )


async def _membership_row(pool, *, tenant_id, subject_id):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT id, state, role, revision FROM tenant_membership "
            "WHERE tenant_id = $1 AND subject_id = $2",
            tenant_id,
            subject_id,
        )


async def test_grant_as_member_rejected(pool, repo):
    """MEMBER 역할 사용자는 grant_membership을 호출할 수 없다."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    member_ctx = _ctx(tenant_id=owner_id, subject_id=member_id, role="MEMBER", mfa_verified=True)

    new_subject = uuid4()
    with pytest.raises(GrantAuthorizationError):
        await grant_membership(
            repo, pool, member_ctx, subject_id=new_subject, role=MembershipRole.MEMBER
        )

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=new_subject)
    assert row is None


async def test_suspend_unknown_target_rejected(pool, repo):
    """Suspending a subject with no membership raises SuspendTargetNotFoundError."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    ctx = _ctx(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    ghost = uuid4()

    with pytest.raises(SuspendTargetNotFoundError):
        await suspend_membership(repo, pool, ctx, subject_id=ghost)

    assert await _membership_row(pool, tenant_id=owner_id, subject_id=ghost) is None


async def test_suspend_as_member_rejected(pool, repo):
    """MEMBER 역할 사용자는 멤버십을 정지할 수 없다."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    target_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.AUDITOR)

    member_ctx = _ctx(tenant_id=owner_id, subject_id=member_id, role="MEMBER", mfa_verified=True)

    with pytest.raises(SuspendAuthorizationError):
        await suspend_membership(repo, pool, member_ctx, subject_id=target_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=target_id)
    assert row is not None
    assert row["state"] == "ACTIVE"


async def test_revoke_as_member_rejected(pool, repo):
    """MEMBER 역할 사용자는 멤버십을 박탈할 수 없다."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    target_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.AUDITOR)

    member_ctx = _ctx(tenant_id=owner_id, subject_id=member_id, role="MEMBER", mfa_verified=True)

    with pytest.raises(RevokeAuthorizationError):
        await revoke_membership(repo, pool, member_ctx, subject_id=target_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=target_id)
    assert row is not None
    assert row["state"] == "ACTIVE"


async def test_revoke_unknown_target_rejected(pool, repo):
    """Revoking a subject with no membership raises RevokeTargetNotFoundError."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    ghost = uuid4()
    ctx = _ctx(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)

    with pytest.raises(RevokeTargetNotFoundError):
        await revoke_membership(repo, pool, ctx, subject_id=ghost)

    assert await _membership_row(pool, tenant_id=owner_id, subject_id=ghost) is None


async def test_grant_as_admin_without_mfa_rejected(pool, repo):
    """MFA 미검증 ADMIN은 멤버십을 부여할 수 없다."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _ctx(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=False)

    new_subject = uuid4()
    with pytest.raises(Exception) as exc_info:
        await grant_membership(
            repo, pool, admin_ctx, subject_id=new_subject, role=MembershipRole.MEMBER
        )
    assert "mfa" in str(exc_info.value).lower() or "MFA" in str(exc_info.value)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=new_subject)
    assert row is None


async def test_grant_failure_injection_rolls_back(pool, repo, monkeypatch):
    """insert_membership가 RuntimeError를 raise하면 전체 트랜잭션이 롤백된다."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _ctx(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    target = uuid4()
    original_insert = repo.insert_membership

    async def patched_insert(conn, *, tenant_id, subject_id, role, created_by):
        if subject_id == target:
            raise RuntimeError("injected DB failure")
        return await original_insert(
            conn, tenant_id=tenant_id, subject_id=subject_id, role=role, created_by=created_by
        )

    monkeypatch.setattr(repo, "insert_membership", patched_insert)

    with pytest.raises(RuntimeError, match="injected DB failure"):
        await grant_membership(repo, pool, admin_ctx, subject_id=target, role=MembershipRole.MEMBER)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=target)
    assert row is None


async def _one_suspend(pool, repo, owner_id, admin_ctx, suspend=suspend_membership):
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    return await suspend(repo, pool, admin_ctx, subject_id=member_id)


@pytest.mark.perf
async def test_suspend_membership_wall_ms_under_budget(pool, repo):
    """suspend_membership best_of(15) wall_ms < 60 ms (local best_of_5 = 1.941 ms, 30x+ margin)."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    ctx = _ctx(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    sample = await PerfBudget().best_of_async(
        lambda: _one_suspend(pool, repo, owner_id, ctx), n=15, warmup=2
    )

    assert sample.wall_ms < SUSPEND_WALL_MS_BUDGET, (
        f"suspend_membership wall_ms={sample.wall_ms:.3f} ms exceeds {SUSPEND_WALL_MS_BUDGET} ms"
    )


@pytest.mark.perf
async def test_suspend_budget_gate_fails_on_injected_regression(pool, repo):
    """Gate-red: a virtual 10x-budget delay makes the same assertion fail."""
    reset_virtual_totals()
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    ctx = _ctx(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    slow = virtual_delayed_async(suspend_membership, SUSPEND_WALL_MS_BUDGET * 10 / 1000)

    sample = await PerfBudget().best_of_async(
        lambda: _one_suspend(pool, repo, owner_id, ctx, suspend=slow), n=5, warmup=1
    )

    assert sample.wall_ms >= SUSPEND_WALL_MS_BUDGET * 10
    with pytest.raises(AssertionError):
        assert sample.wall_ms < SUSPEND_WALL_MS_BUDGET
