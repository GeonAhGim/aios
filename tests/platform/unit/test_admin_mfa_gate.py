"""PLT-35(task-2682, fixed task-6482) -- `get_current_mfa_admin`의 MFA 게이트
단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2(B) row
`src/api/admin_deps.py` ("`get_current_admin`은 `auth_level ==
"MFA_VERIFIED"` 필수"). 실제 배선은 `get_current_mfa_admin`(admin_deps.py의
docstring 참조 -- 기존 `get_current_admin`은 15개 이상 라우트의 회귀를 피하려
그대로 두고, 새 의존성에서만 강제한다).

task-3795(REJECT) 반영: 게이트는 더 이상 JWT `auth_level` 클레임(로그인/
refresh 시점에 고정, 최대 REFRESH_TTL_DAYS=14일 그대로 유지됨)을 보지 않고
매 요청마다 DB에서 새로 읽은 `mfa_verified_at`이 `MFA_STEP_UP_WINDOW`(15분)
안에 있는지를 재확인한다 -- `test_stale_mfa_verified_at_rejected`가 이
task-6482 DoD 항목("mfa_verified_at이 15분 창을 벗어난 상태로 grant 소비
시도 시 403")을 게이트 단위에서 증명한다.

게이트 적색 재현(ADR-2026-09-09-C D2): `test_never_verified_admin_rejected`가
이 리프 이전 상태(관리자 MFA 검사가 전혀 없어 `is_platform_admin`만 확인하면
MFA 여부와 무관하게 통과)를 재현한다 -- `get_current_mfa_admin` 없이
`get_current_admin`만 거쳤다면 이 테스트는 (예외 없이) 그냥 통과해버렸을
것이다. 이 파일은 DB가 필요 없다 -- `AuthenticatedUser`를 직접 생성해
의존성 함수를 순수 호출한다(FastAPI DI 체인 우회, 함수 자체의 결정 로직만
검증)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.api.admin_deps import get_current_mfa_admin
from src.api.deps import AuthenticatedUser
from src.core.security.break_glass import AdminMfaRequiredError


def _admin(*, mfa_verified_at: datetime | None) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="admin",
        mfa_enabled=mfa_verified_at is not None,
        mfa_verified_at=mfa_verified_at,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
        session_id=uuid4(),
        # auth_level 클레임은 더 이상 게이트가 보지 않는다(task-3795) -- 항상
        # "MFA_VERIFIED"로 두어, 이 테스트들이 mfa_verified_at 신선도만으로
        # 통과/거부가 갈린다는 것을 명확히 한다.
        auth_level="MFA_VERIFIED",
    )


async def test_fresh_mfa_verified_admin_passes():
    admin = _admin(mfa_verified_at=datetime.now(timezone.utc))
    result = await get_current_mfa_admin(admin=admin)
    assert result is admin


async def test_never_verified_admin_rejected():
    """게이트 적색 재현 -- MFA 게이트가 없던 상태(is_platform_admin만 확인)라면
    MFA를 전혀 거치지 않은 관리자도 그대로 통과했을 시나리오."""
    admin = _admin(mfa_verified_at=None)
    with pytest.raises(AdminMfaRequiredError, match=str(admin.user_id)):
        await get_current_mfa_admin(admin=admin)


async def test_stale_mfa_verified_at_rejected():
    """task-3795/task-6482 DoD -- 16분 전에 TOTP를 통과한 세션은 JWT
    `auth_level`이 여전히 "MFA_VERIFIED"라 하더라도(로그인 이후 refresh로
    최대 14일간 그 값을 그대로 들고 다닐 수 있음) 403이어야 한다."""
    admin = _admin(mfa_verified_at=datetime.now(timezone.utc) - timedelta(minutes=16))
    with pytest.raises(AdminMfaRequiredError, match=str(admin.user_id)):
        await get_current_mfa_admin(admin=admin)


async def test_mfa_verified_at_exactly_at_window_boundary_passes():
    admin = _admin(mfa_verified_at=datetime.now(timezone.utc) - timedelta(minutes=14, seconds=59))
    result = await get_current_mfa_admin(admin=admin)
    assert result is admin
