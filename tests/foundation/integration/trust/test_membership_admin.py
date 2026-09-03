"""grant/suspend/revoke_membership 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-29.
DoD(task-1103): last-owner 강등·삭제 거부, revoke/suspend의 세션 폐기 부작용,
cross-tenant 403이 각각 실 DB로 단언된다.
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest
from fastapi import HTTPException

from src.api.foundation_deps import get_tenant_context
from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.trust.adapters.postgres_membership_repository import (
    PostgresMembershipRepository,
)
from src.foundation.trust.application.grant_membership import (
    GrantAuthorizationError,
    MembershipMfaRequiredError,
    grant_membership,
)
from src.foundation.trust.application.revoke_membership import (
    RevokeAuthorizationError,
    RevokeLastOwnerError,
    RevokeTargetNotFoundError,
    revoke_membership,
)
from src.foundation.trust.application.suspend_membership import (
    SuspendLastOwnerError,
    suspend_membership,
)
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.domain.models import MembershipRole, MembershipState, TenantKind
from src.services.auth_service import User
from tests.integration.conftest import create_test_user


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


def _context(*, tenant_id: UUID, subject_id: UUID, role: str, mfa_verified: bool) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id, subject_id=subject_id, role=role, mfa_verified=mfa_verified
    )


async def _household(pool, repo) -> UUID:
    """OWNER 한 명뿐인 HOUSEHOLD tenant(tenant_id == owner의 user_id)."""
    owner_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await repo.insert_tenant(conn, tenant_id=owner_id, kind=TenantKind.HOUSEHOLD)
        await repo.insert_membership(
            conn, tenant_id=owner_id, subject_id=owner_id,
            role=MembershipRole.OWNER, created_by=owner_id,
        )
    return owner_id


async def _add_member(pool, repo, *, tenant_id: UUID, role: MembershipRole) -> UUID:
    """setup 전용(grant_membership 유스케이스를 거치지 않고 직접 행을 만든다)."""
    member_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        await repo.insert_membership(
            conn, tenant_id=tenant_id, subject_id=member_id, role=role, created_by=tenant_id
        )
    return member_id


async def _insert_active_session(pool, *, user_id: UUID, tenant_id: UUID) -> UUID:
    refresh_hash = hashlib.sha256(uuid4().bytes).hexdigest()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO auth_session (user_id, tenant_id, refresh_hash, expires_at) "
            "VALUES ($1, $2, $3, $4) RETURNING id",
            user_id,
            tenant_id,
            refresh_hash,
            datetime.now(timezone.utc) + timedelta(days=1),
        )
    session_id: UUID = row["id"]
    return session_id


async def _session_revoked(pool, session_id: UUID) -> bool:
    async with pool.acquire() as conn:
        revoked_at = await conn.fetchval(
            "SELECT revoked_at FROM auth_session WHERE id = $1", session_id
        )
    return revoked_at is not None


async def _membership_row(pool, *, tenant_id: UUID, subject_id: UUID):
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT state, revision FROM tenant_membership "
            "WHERE tenant_id = $1 AND subject_id = $2 ORDER BY created_at DESC LIMIT 1",
            tenant_id,
            subject_id,
        )


# GrantMembership --------------------------------------------------------


async def test_grant_membership_creates_active_membership(pool, repo):
    owner_id = await _household(pool, repo)
    new_member = await create_test_user(pool)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)

    membership = await grant_membership(
        repo, pool, owner_ctx, subject_id=new_member, role=MembershipRole.MEMBER
    )

    assert membership.state == MembershipState.ACTIVE
    assert membership.role == MembershipRole.MEMBER
    assert membership.revision == 1


async def test_grant_membership_without_mfa_raises():
    owner_id = uuid4()
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=False)

    with pytest.raises(MembershipMfaRequiredError):
        await grant_membership(
            None, None, owner_ctx, subject_id=uuid4(), role=MembershipRole.MEMBER  # type: ignore[arg-type]
        )


async def test_grant_membership_duplicate_active_raises_concurrency_conflict(pool, repo):
    owner_id = await _household(pool, repo)
    new_member = await create_test_user(pool)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)
    await grant_membership(repo, pool, owner_ctx, subject_id=new_member, role=MembershipRole.MEMBER)

    with pytest.raises(ConcurrencyConflictError):
        await grant_membership(
            repo, pool, owner_ctx, subject_id=new_member, role=MembershipRole.MEMBER
        )


async def test_regrant_after_revoke_requires_owner_role(pool, repo):
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    target_id = await create_test_user(pool)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    await grant_membership(repo, pool, owner_ctx, subject_id=target_id, role=MembershipRole.MEMBER)
    await revoke_membership(repo, pool, owner_ctx, subject_id=target_id)

    with pytest.raises(GrantAuthorizationError):
        await grant_membership(
            repo, pool, admin_ctx, subject_id=target_id, role=MembershipRole.MEMBER
        )

    regranted = await grant_membership(
        repo, pool, owner_ctx, subject_id=target_id, role=MembershipRole.MEMBER
    )
    assert regranted.state == MembershipState.ACTIVE
    assert regranted.revision == 1  # 새 행(73번 §4.1 "ACTIVE(새 revision)")


# last-owner 거부 ---------------------------------------------------------


async def test_suspend_last_owner_is_rejected(pool, repo):
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    with pytest.raises(SuspendLastOwnerError):
        await suspend_membership(repo, pool, admin_ctx, subject_id=owner_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=owner_id)
    assert row["state"] == "ACTIVE"
    assert row["revision"] == 1


async def test_revoke_last_owner_is_rejected(pool, repo):
    owner_id = await _household(pool, repo)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)

    with pytest.raises(RevokeLastOwnerError):
        await revoke_membership(repo, pool, owner_ctx, subject_id=owner_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=owner_id)
    assert row["state"] == "ACTIVE"
    assert row["revision"] == 1


# 세션 폐기 부작용 --------------------------------------------------------


async def test_suspend_membership_revokes_target_sessions(pool, repo):
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    session_id = await _insert_active_session(pool, user_id=member_id, tenant_id=member_id)

    updated = await suspend_membership(repo, pool, admin_ctx, subject_id=member_id)

    assert updated.state == MembershipState.SUSPENDED
    assert updated.revision == 2
    assert await _session_revoked(pool, session_id) is True


async def test_revoke_membership_revokes_target_sessions(pool, repo):
    owner_id = await _household(pool, repo)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)
    session_id = await _insert_active_session(pool, user_id=member_id, tenant_id=member_id)

    updated = await revoke_membership(repo, pool, owner_ctx, subject_id=member_id)

    assert updated.state == MembershipState.REVOKED
    assert await _session_revoked(pool, session_id) is True


async def test_revoke_membership_not_found_raises(pool, repo):
    owner_id = await _household(pool, repo)
    owner_ctx = _context(tenant_id=owner_id, subject_id=owner_id, role="OWNER", mfa_verified=True)

    with pytest.raises(RevokeTargetNotFoundError):
        await revoke_membership(repo, pool, owner_ctx, subject_id=uuid4())


async def test_revoke_membership_by_unauthorized_role_is_forbidden(pool, repo):
    owner_id = await _household(pool, repo)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    other = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    member_ctx = _context(
        tenant_id=owner_id, subject_id=member_id, role="MEMBER", mfa_verified=True
    )

    with pytest.raises(RevokeAuthorizationError):
        await revoke_membership(repo, pool, member_ctx, subject_id=other)


# cross-tenant 403 — router가 의존하는 get_tenant_context(PLT-28)를 우회하지
# 않는지 실 DB로 확인한다(decision: "X-Tenant-Id는 라우터가 직접 읽지 않고
# PLT-28 컨텍스트만 신뢰").


class _FakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def _auth_user(user_id: UUID) -> User:
    return User(
        user_id=user_id, email=f"{user_id}@example.com", display_name=None,
        mfa_enabled=False, mfa_verified_at=None, status="ACTIVE",
        is_verifier=False, is_platform_admin=False,
    )


async def test_cross_tenant_header_is_rejected_before_membership_commands_run(pool, repo):
    victim_owner_id = await _household(pool, repo)
    attacker_id = await create_test_user(pool)  # victim tenant에 멤버십 없음

    request = _FakeRequest({"X-Tenant-Id": str(victim_owner_id)})

    with pytest.raises(HTTPException) as excinfo:
        await get_tenant_context(
            request,  # type: ignore[arg-type]
            user=_auth_user(attacker_id),
            pool=pool,
            membership_repo=repo,
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail["error_code"] == "AUTH_TENANT_MISMATCH"

    # 공격 시도 이후에도 피해자 tenant의 멤버십은 그대로다.
    row = await _membership_row(pool, tenant_id=victim_owner_id, subject_id=victim_owner_id)
    assert row["state"] == "ACTIVE"
