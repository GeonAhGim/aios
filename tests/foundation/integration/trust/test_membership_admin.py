"""grant/suspend/revoke_membership 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-29.
DoD(task-1103): last-owner 강등·삭제 거부, revoke/suspend의 세션 폐기 부작용,
cross-tenant 403이 각각 실 DB로 단언된다.

DEEPEN(task-3175)이 지적한 공백(실패 주입 없음, 수치 성능 단언 없음, 게이트
적색 재현 없음, 적대 테스트가 cross-tenant 403 1건뿐)을 아래로 메운다:
`test_suspend_membership_rolls_back_on_repository_failure_after_owner_lock`,
`test_suspend_membership_state_stays_committed_when_logout_all_fails`(실패
주입 2건), `test_suspend_membership_p95_under_borrowed_order_ack_budget`,
`test_suspend_membership_budget_gate_fails_on_injected_regression`(수치
성능 단언·게이트 적색 재현), `test_concurrent_suspend_of_both_owners_leaves_
exactly_one_active_owner`(D3 적대적 동시성 — 73번 I4 last-owner 불변식이
실제 경합에서도 유지되는지 증명).
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import time
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
            conn,
            tenant_id=owner_id,
            subject_id=owner_id,
            role=MembershipRole.OWNER,
            created_by=owner_id,
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
            None,
            None,
            owner_ctx,
            subject_id=uuid4(),
            role=MembershipRole.MEMBER,  # type: ignore[arg-type]
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
        user_id=user_id,
        email=f"{user_id}@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
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


# 실패 주입 ----------------------------------------------------------------


async def test_suspend_membership_rolls_back_on_repository_failure_after_owner_lock(
    pool, repo, monkeypatch
):
    """실패 주입 1건 — `count_active_owners`의 FOR UPDATE 잠금을 획득한
    뒤, 상태 UPDATE 직전에 저장소 계층에서 미분류 예외(`EXCEPTION_MAP`에
    없는 `RuntimeError`)가 터지면 트랜잭션 전체가 롤백되어 멤버십 행이
    ACTIVE/revision=1로 그대로 남고, 커밋 후 부작용인 `logout_all`은 아예
    호출되지 않아 세션도 살아있어야 한다(위장 성공 없음, fail-closed)."""
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    session_id = await _insert_active_session(pool, user_id=member_id, tenant_id=member_id)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected repository failure")

    monkeypatch.setattr(repo, "update_conditional_membership_state", _boom)

    with pytest.raises(RuntimeError, match="injected repository failure"):
        await suspend_membership(repo, pool, admin_ctx, subject_id=member_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=member_id)
    assert row["state"] == "ACTIVE"
    assert row["revision"] == 1
    assert await _session_revoked(pool, session_id) is False


async def test_suspend_membership_state_stays_committed_when_logout_all_fails(
    pool, repo, monkeypatch
):
    """실패 주입 2건 — `suspend_membership.py`의 상태 전이 트랜잭션은 이미
    커밋된 뒤 `logout_all`(PLT-24)을 별도로 호출한다. 그 호출이 미분류
    예외로 실패하면, 이미 커밋된 SUSPENDED 상태는 되돌아가지 않고(트랜잭션
    밖이므로 롤백 대상이 아니다) 예외가 호출자에게 그대로 전파되어 위장
    성공(200 OK)을 반환하지 않는다는 실제 경계 동작을 실DB로 고정한다."""
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    member_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.MEMBER)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    session_id = await _insert_active_session(pool, user_id=member_id, tenant_id=member_id)

    async def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected logout_all failure")

    monkeypatch.setattr("src.foundation.trust.application.suspend_membership.logout_all", _boom)

    with pytest.raises(RuntimeError, match="injected logout_all failure"):
        await suspend_membership(repo, pool, admin_ctx, subject_id=member_id)

    row = await _membership_row(pool, tenant_id=owner_id, subject_id=member_id)
    assert row["state"] == "SUSPENDED"
    assert row["revision"] == 2
    assert await _session_revoked(pool, session_id) is False


# 수치 성능 단언·게이트 적색 재현 ------------------------------------------


async def _suspend_membership_p95_ms(pool, repo, admin_ctx, tenant_id, *, n: int) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        member_id = await _add_member(pool, repo, tenant_id=tenant_id, role=MembershipRole.MEMBER)
        start = time.perf_counter()
        await suspend_membership(repo, pool, admin_ctx, subject_id=member_id)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[math.ceil(0.95 * len(durations_ms)) - 1]


@pytest.mark.perf
async def test_suspend_membership_p95_under_borrowed_order_ack_budget(pool, repo):
    """수치 성능 단언 1건 — ADR-2026-09-09-C Decision 1 예산표에 멤버십
    상태전이 전용 항목은 없다. `suspend_membership`은 조회+FOR UPDATE
    잠금+조건부 UPDATE+`logout_all`까지 묶은 단일 유스케이스 왕복이라는
    점에서 가장 가까운 유사 항목("주문 제출→ACK p95 50ms(paper)")을 자체
    예산으로 차용한다(task-3162/3168/3173 DEEPEN과 동일 차용 근거).
    suspend_membership 30회 반복 p95를 그 예산 내로 단언한다."""
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    p95_ms = await _suspend_membership_p95_ms(pool, repo, admin_ctx, owner_id, n=30)

    assert p95_ms < 50.0


async def test_suspend_membership_budget_gate_fails_on_injected_regression(pool, repo, monkeypatch):
    """게이트 적색 재현 — 위 p95 단언이 실제로 회귀를 잡는지 확인한다.
    `suspend_membership`이 쓰는 `asyncpg.Connection.fetchrow`(조회/조건부
    UPDATE 둘 다 경유)에 60ms 인위 지연을 주입해, 같은 측정 로직이 실제로
    AssertionError를 내는지 본다(tautology 아님을 증명)."""
    owner_id = await _household(pool, repo)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)
    original_fetchrow = asyncpg.Connection.fetchrow

    async def _slow_fetchrow(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_fetchrow(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _slow_fetchrow)

    p95_ms = await _suspend_membership_p95_ms(pool, repo, admin_ctx, owner_id, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < 50.0


# D3 적대적 동시성 ----------------------------------------------------------


async def test_concurrent_suspend_of_both_owners_leaves_exactly_one_active_owner(pool, repo):
    """D3 적대적 동시성 증거 — 73번 I4("tenant당 ACTIVE OWNER >= 1")가
    순차 호출이 아니라 실제 경합 아래서도 유지되는지 증명한다. 기존
    negative test(`test_suspend_last_owner_is_rejected`)는 OWNER가 이미
    1명뿐일 때만 확인한다 — 이 테스트는 OWNER 2명이 있는 tenant에서 동일한
    ADMIN 컨텍스트로 두 OWNER를 동시에 정지시켜, `count_active_owners`의
    FOR UPDATE 잠금이 경합 중에도 fail-closed로 직렬화되어 정확히 1명만
    성공(다른 1명은 `SuspendLastOwnerError`)하고, 두 OWNER가 동시에 정지되어
    tenant가 OWNER 0명으로 남는 경우가 실제로 발생하지 않음을 실DB로
    확인한다."""
    owner_id = await _household(pool, repo)
    second_owner_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.OWNER)
    admin_id = await _add_member(pool, repo, tenant_id=owner_id, role=MembershipRole.ADMIN)
    admin_ctx = _context(tenant_id=owner_id, subject_id=admin_id, role="ADMIN", mfa_verified=True)

    async def _attempt(subject_id: UUID):
        try:
            return await suspend_membership(repo, pool, admin_ctx, subject_id=subject_id)
        except SuspendLastOwnerError:
            return None

    results = await asyncio.gather(_attempt(owner_id), _attempt(second_owner_id))
    winners = [r for r in results if r is not None]

    assert len(winners) == 1
    assert winners[0].state == MembershipState.SUSPENDED

    async with pool.acquire() as conn:
        active_owners = await repo.count_active_owners(conn, owner_id)
    assert active_owners == 1
