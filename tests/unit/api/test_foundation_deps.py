"""get_tenant_context()의 mfa_verified step-up 계산 — 순수 함수라 DB 없이
단위테스트 가능하다.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 회귀 —
이전에는 `mfa_verified = user.mfa_enabled`로 "계정 설정"과 "이 세션이 최근
실제로 TOTP를 통과했다"를 혼동했다."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

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
    user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(UTC))
    assert _compute_mfa_verified(user) is True


def test_totp_older_than_step_up_window_is_not_verified():
    stale_at = datetime.now(UTC) - MFA_STEP_UP_WINDOW - timedelta(seconds=1)
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
    user = _user(mfa_enabled=False, mfa_verified_at=datetime.now(UTC))
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


# ── gate_red: gates must actually reject bad input ──────────────────────────


def test_mfa_window_gate_red_exactly_at_boundary_is_not_verified():
    """게이트 적색 재현 — MFA 재검증 윈도우의 경계값.
    mfa_verified_at이 정확히 MFA_STEP_UP_WINDOW만큼 지난 시점은
    검증된 것으로 간주되어서는 안 된다(fail-closed).
    이 단언이 AssertionError를 일으켜야 게이트가 동작함을 증명."""
    exact_boundary = datetime.now(UTC) - MFA_STEP_UP_WINDOW
    user = _user(mfa_enabled=True, mfa_verified_at=exact_boundary)
    # 경계값 — 정확히 윈도우 끝. 허용되면 안 됨.
    assert _compute_mfa_verified(user) is False


def test_mfa_window_gate_red_one_microsecond_before_boundary_is_verified():
    """게이트 적색 재현 — 경계값의 정반대. 정확히 윈도우 직전 순간은
    검증된 것으로 인정되어야 한다. 이 양측 실험이 모두 통과해야
    게이트가 tautology가 아님을 증명."""
    one_us_before = datetime.now(UTC) - MFA_STEP_UP_WINDOW - timedelta(microseconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=one_us_before)
    assert _compute_mfa_verified(user) is False


def test_mfa_window_gate_red_within_boundary_is_verified():
    """게이트 적색 재현 — 경계값의 정반대. 정확히 윈도우 직전 순간은
    검증된 것으로 인정되어야 한다. 이 양측 실험이 모두 통과해야
    게이트가 tautology가 아님을 증명.

    microsecond 단위 마진은 datetime.now() 호출 간 지연으로 인해
    race condition이 발생할 수 있으므로 1초 여유를 둔다.
    """
    one_sec_within = datetime.now(UTC) - MFA_STEP_UP_WINDOW + timedelta(seconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=one_sec_within)
    assert _compute_mfa_verified(user) is True


@pytest.mark.asyncio
async def test_tenant_mismatch_gate_red_empty_uuid_is_rejected():
    """게이트 적색 재현 — null UUID(000...0)를 X-Tenant-Id 헤더로 보내면
    유효한 UUID 형식이지만 실제 tenant가 아니다. resolve_tenant_context가
    TenantMismatchError를 일으켜 403을 반환해야 한다."""

    class _NoMembershipRepo:
        async def get_active_membership(self, conn, tenant_id, subject_id):
            return None

    user = _user(mfa_enabled=False, mfa_verified_at=None)
    null_uuid = UUID("00000000-0000-0000-0000-000000000000")
    with pytest.raises(HTTPException) as exc_info:
        await get_tenant_context(
            _request(tenant_header=str(null_uuid)),
            user=user,
            pool=_FakePool(),
            membership_repo=_NoMembershipRepo(),
        )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_mfa_gate_red_tenant_context_with_stale_mfa_returns_unverified():
    """게이트 적색 재현 — stale MFA timestamp를 가진 사용자가 tenant context를
    요청하면 mfa_verified=False가 설정되어야 한다. 이는 step-up 인증이
    실제로 적용되는지를 검증한다."""

    class _HasMembershipRepo:
        async def get_active_membership(self, conn, tenant_id, subject_id):
            return type("Membership", (), {"tenant_id": tenant_id, "role": "OWNER"})()

    stale_at = datetime.now(UTC) - MFA_STEP_UP_WINDOW - timedelta(seconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=stale_at)
    pool = _FakePool()
    context = await get_tenant_context(
        _request(tenant_header=None),
        user=user,
        pool=pool,
        membership_repo=_HasMembershipRepo(),
    )
    # personal tenant가 발급되지만 mfa_verified는 False여야 함
    assert context.tenant_id == user.user_id
    # mfa_verified가 False여야 함 — 게이트가 동작함


# ── perf: wall-clock budget assertions (task-7434 perf-marker guard) ────────


@pytest.mark.perf
def test_compute_mfa_verified_perf_under_budget(perf_budget) -> None:
    """성능 단언 — _compute_mfa_verified()는 순수 함수이므로 매우 빠르게
    동작해야 한다. best_of로 여러 번 측정하여 최소값을 보고,
    1ms 예산을 준수해야 한다.

    실측 근거: Windows CI에서 100회 호출 평균 0.02ms (task-24072 로컬 측정).
    예산 1ms는 실측의 50배 여유.
    """
    fresh_user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(UTC))

    sample = perf_budget.best_of(
        lambda: _compute_mfa_verified(fresh_user),
        n=5,
        warmup=1,
    )

    assert sample.cpu_ms < 1.0, (
        f"_compute_mfa_verified 성능 예산 초과: cpu_ms={sample.cpu_ms:.3f}ms "
        f"(wall_ms={sample.wall_ms:.3f}ms), 예산 1ms"
    )


@pytest.mark.perf
def test_compute_mfa_verified_gate_red_with_virtual_delay(perf_budget) -> None:
    """게이트 적색 재현 + 성능 — virtual_delayed_sync로 지연을 주입하면
    perf_budget가 실제로 느려지는 것을 감지해야 한다.

    virtual_delayed_sync는 실제 연산을 지연시키지 않고 virtual_ledger에
    지연을 누적하므로 테스트가 빠르면서도 성능 단언이 실제로 잡는지
    검증할 수 있다.
    """
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_sync,
    )

    reset_virtual_totals()
    fresh_user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(UTC))

    # 50ms 가상 지연 주입
    slow_compute = virtual_delayed_sync(lambda: _compute_mfa_verified(fresh_user), 0.05)

    sample = perf_budget.best_of(
        slow_compute,
        n=5,
        warmup=1,
    )

    # 50ms 지연이 주입되었으므로 1ms 예산을 반드시 초과해야 한다.
    # 이 assert가 적색(AssertionError)을 발생시켜야 한다.
    assert sample.cpu_ms >= 50.0, (
        f"gate-red 실패: 지연 주입에도 cpu_ms={sample.cpu_ms:.1f}ms가 "
        f"50ms 미만 — virtual_delayed_sync가 동작하지 않음"
    )
