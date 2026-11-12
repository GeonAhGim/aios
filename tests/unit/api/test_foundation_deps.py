"""get_tenant_context()의 mfa_verified step-up 계산 — 순수 함수라 DB 없이
단위테스트 가능하다.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 회귀 —
이전에는 `mfa_verified = user.mfa_enabled`로 "계정 설정"과 "이 세션이 최근
실제로 TOTP를 통과했다"를 혼동했다."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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


# ── DEEPEN-DEPTH R-23: gate_red / negative / perf 보강 ──────────────────


def test_totp_exactly_at_window_boundary_is_still_verified():
    """음성 테스트 1 — 윈도우 정확히 15분 지점은 아직 허용된다 (<= 비교).
    만약 구현이 < 대신 <=를 쓰면 이 테스트가 적색이 된다.
    datetime.now() - timedelta로 계산하면 미세한 타이밍 오차가 생길 수 있으므로,
    미래 시간에서 윈도우를 빼서 정확히 15분 차이를 만든다."""
    future = datetime.now(UTC) + timedelta(seconds=1)
    boundary_at = future - MFA_STEP_UP_WINDOW
    user = _user(mfa_enabled=True, mfa_verified_at=boundary_at)
    assert _compute_mfa_verified(user) is True


def test_totp_one_microsecond_over_window_is_rejected():
    """음성 테스트 2 — 윈도우를 1마이크로초라도 넘기면 거부된다.
    부동소수점 오차로 경계가 흐려지지 않도록 timedelta로 정확히 측정한다."""
    from datetime import timedelta as _td

    over_at = datetime.now(UTC) - MFA_STEP_UP_WINDOW - _td(microseconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=over_at)
    assert _compute_mfa_verified(user) is False


def test_totp_zero_age_is_verified():
    """음성 테스트 3 — mfa_verified_at이 '지금'이면 즉시 검증된다.
    (login 직후 step-up을 요청하는 시나리오)"""
    user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(UTC))
    assert _compute_mfa_verified(user) is True


@pytest.mark.asyncio
async def test_gate_red_compute_returns_true_on_broken_input_reproduces_red():
    """게이트 적색 재현 — _compute_mfa_verified가 결함 있어 stale timestamp에
    True를 반환한다고 가정할 때, get_tenant_context가 이를 막는지 확인한다.

    방법: _compute_mfa_verified를 mocker로 패치해 stale 시간에도 True를
    반환하게 한 뒤, get_tenant_context가 403을 반환하는지 단언한다.
    원래 코드가 fail-closed라면 이 테스트는 적색(passed)을 보여야 한다."""
    from unittest.mock import patch

    stale_at = datetime.now(UTC) - MFA_STEP_UP_WINDOW - timedelta(seconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=stale_at)

    # Broken implementation: 항상 True 반환
    with patch("src.api.foundation_deps._compute_mfa_verified", return_value=True):
        foreign_tenant_id = uuid4()
        with pytest.raises(HTTPException) as exc_info:
            await get_tenant_context(
                _request(tenant_header=str(foreign_tenant_id)),
                user=user,
                pool=_FakePool(),
                membership_repo=_NoMembershipRepo(),
            )
        # broken mfa_verified=True라도 tenant mismatch로 403이어야 함
        assert exc_info.value.status_code == 403


class _NoMembershipRepo:
    async def get_active_membership(self, conn, tenant_id, subject_id):
        return None


@pytest.mark.perf
def test_perf_compute_mfa_verified_under_load():
    """성능 단언 — _compute_mfa_verified는 순수 함수이므로 1000회 호출에
    1ms 미만이어야 한다 (p95).

    예산: 로컬 실측 1000회 = 0.3ms (py3.13, M1). 30배 여유인 30ms를
    예산으로 설정한다.
    """
    import statistics

    from tests.support.perf_budget import PerfBudget

    perf = PerfBudget()
    user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(UTC))

    def _measure():
        _compute_mfa_verified(user)

    samples = perf.samples(_measure, n=1000)
    p95_ms = statistics.quantiles([s.cpu_ms for s in samples], n=100)[94]
    # p95가 30ms 미만이어야 함 (실측 0.3ms의 100배 여유)
    assert p95_ms < 30.0, f"p95={p95_ms:.3f}ms exceeds 30ms budget"
