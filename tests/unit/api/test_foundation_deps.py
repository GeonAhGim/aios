"""get_tenant_context()의 mfa_verified step-up 계산 — 순수 함수라 DB 없이
단위테스트 가능하다.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 회귀 —
이전에는 `mfa_verified = user.mfa_enabled`로 "계정 설정"과 "이 세션이 최근
실제로 TOTP를 통과했다"를 혼동했다."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import asyncpg
import pytest
from fastapi import HTTPException, Request

from src.api.foundation_deps import MFA_STEP_UP_WINDOW, _compute_mfa_verified, get_tenant_context
from src.services.auth_service import User


def _user(*, mfa_enabled: bool, mfa_verified_at: datetime | None) -> User:
    return User(
        user_id=uuid4(),
        email="test@example.com",
        display_name=None,
        mfa_enabled=mfa_enabled,
        mfa_verified_at=mfa_verified_at,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=False,
    )


def _request(*, tenant_header: str | None) -> Request:
    headers = [(b"x-tenant-id", tenant_header.encode())] if tenant_header else []
    scope = {"type": "http", "headers": headers, "method": "GET", "path": "/"}
    return Request(scope)


class _FakePool:
    """asyncpg.Pool의 `acquire()` async context manager만 흉내낸다 —
    `get_tenant_context`는 이 conn을 그대로 membership repo에 넘길 뿐,
    직접 쿼리하지 않는다."""

    def __init__(self) -> None:
        self.acquired = False

    @asynccontextmanager
    async def acquire(self):
        self.acquired = True
        yield object()


class _RaisingMembershipRepo:
    """실패주입 — membership 조회 중 예기치 않은 예외(예: 커넥션 드롭)."""

    async def get_active_membership(self, conn, tenant_id, subject_id):
        raise asyncpg.PostgresConnectionError("connection lost")


def test_recent_totp_within_window_is_verified():
    user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(timezone.utc))
    assert _compute_mfa_verified(user) is True


def test_totp_older_than_step_up_window_is_not_verified():
    stale_at = datetime.now(timezone.utc) - MFA_STEP_UP_WINDOW - timedelta(seconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=stale_at)
    assert _compute_mfa_verified(user) is False


def test_mfa_enabled_but_never_verified_is_not_verified():
    """마이그레이션 이전 계정(cdd905e63ffe) — mfa_enabled=True인데
    mfa_verified_at이 아직 NULL. fail-closed."""
    user = _user(mfa_enabled=True, mfa_verified_at=None)
    assert _compute_mfa_verified(user) is False


def test_mfa_disabled_is_never_verified_even_with_a_recent_timestamp():
    """mfa_enabled=False면 애초에 검증할 대상이 없다 — timestamp가 있어도
    (예: 과거에 MFA를 껐다 켰다 한 흔적) 무시한다."""
    user = _user(mfa_enabled=False, mfa_verified_at=datetime.now(timezone.utc))
    assert _compute_mfa_verified(user) is False


@pytest.mark.asyncio
async def test_malformed_tenant_id_header_is_rejected_with_400():
    """불변식 위반 입력 — `X-Tenant-Id`가 UUID 형식이 아니면 조회를
    시도하지 않고 즉시 400 VALIDATION_INVALID_FIELD로 거부한다(fail-closed)."""
    user = _user(mfa_enabled=False, mfa_verified_at=None)
    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_context(
            _request(tenant_header="not-a-uuid"),
            user=user,
            pool=_FakePool(),
            membership_repo=_RaisingMembershipRepo(),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_non_member_requesting_foreign_tenant_gets_403_mismatch():
    """불변식 위반 입력 — 요청한 tenant_id에 활성 멤버십이 없는 사용자는
    personal tenant로 폴백하지 않고 403 AUTH_TENANT_MISMATCH로 거부한다."""

    class _NoMembershipRepo:
        async def get_active_membership(self, conn, tenant_id, subject_id):
            return None

    user = _user(mfa_enabled=False, mfa_verified_at=None)
    foreign_tenant_id = uuid4()
    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_context(
            _request(tenant_header=str(foreign_tenant_id)),
            user=user,
            pool=_FakePool(),
            membership_repo=_NoMembershipRepo(),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_tenant_header_issues_personal_tenant_without_membership_lookup():
    """헤더가 없으면 personal tenant(id == user_id)를 바로 발급 — membership
    repo를 조회하지 않는다(positive control for the two negative cases above)."""
    user = _user(mfa_enabled=False, mfa_verified_at=None)
    pool = _FakePool()
    context = await get_tenant_context(
        _request(tenant_header=None),
        user=user,
        pool=pool,
        membership_repo=_RaisingMembershipRepo(),
    )
    assert context.tenant_id == user.user_id
    assert context.role == "OWNER"


@pytest.mark.asyncio
async def test_membership_lookup_failure_propagates_instead_of_silently_granting_access():
    """실패주입 — membership repo가 커넥션 오류로 예외를 던지면 그대로
    전파돼야 한다. 여기서 삼켜서 personal/기본 tenant를 발급하면 멤버십
    미확인 상태로 접근을 허용하는 fail-open 회귀가 된다."""
    user = _user(mfa_enabled=False, mfa_verified_at=None)
    with pytest.raises(asyncpg.PostgresConnectionError):
        await get_tenant_context(
            _request(tenant_header=str(uuid4())),
            user=user,
            pool=_FakePool(),
            membership_repo=_RaisingMembershipRepo(),
        )
