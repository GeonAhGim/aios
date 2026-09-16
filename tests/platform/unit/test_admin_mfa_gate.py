"""PLT-35(task-2682) -- `get_current_mfa_admin`의 MFA 게이트 단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2(B) row
`src/api/admin_deps.py` ("`get_current_admin`은 `auth_level ==
"MFA_VERIFIED"` 필수"). 실제 배선은 `get_current_mfa_admin`(admin_deps.py의
docstring 참조 -- 기존 `get_current_admin`은 15개 이상 라우트의 회귀를 피하려
그대로 두고, 새 의존성에서만 강제한다).

게이트 적색 재현(ADR-2026-09-09-C D2): `test_password_level_admin_rejected`가
이 리프 이전 상태(관리자 MFA 검사가 전혀 없어 `is_platform_admin`만 확인하면
어떤 auth_level이든 통과)를 재현한다 -- `get_current_mfa_admin` 없이
`get_current_admin`만 거쳤다면 이 테스트는 (예외 없이) 그냥 통과해버렸을
것이다. 이 파일은 DB가 필요 없다 -- `AuthenticatedUser`를 직접 생성해
의존성 함수를 순수 호출한다(FastAPI DI 체인 우회, 함수 자체의 결정 로직만
검증)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.api.admin_deps import get_current_mfa_admin
from src.api.deps import AuthenticatedUser
from src.core.security.break_glass import AdminMfaRequiredError


def _admin(*, auth_level: str) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=uuid4(),
        email="admin@example.com",
        display_name="admin",
        mfa_enabled=True,
        mfa_verified_at=datetime.now(timezone.utc),
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
        session_id=uuid4(),
        auth_level=auth_level,
    )


async def test_mfa_verified_admin_passes():
    admin = _admin(auth_level="MFA_VERIFIED")
    result = await get_current_mfa_admin(admin=admin)
    assert result is admin


async def test_password_level_admin_rejected():
    """게이트 적색 재현 -- MFA 게이트가 없던 상태(is_platform_admin만 확인)라면
    PASSWORD 레벨 토큰도 그대로 통과했을 시나리오."""
    admin = _admin(auth_level="PASSWORD")
    with pytest.raises(AdminMfaRequiredError, match=str(admin.user_id)):
        await get_current_mfa_admin(admin=admin)
