"""get_tenant_context()의 mfa_verified step-up 계산 — 순수 함수라 DB 없이
단위테스트 가능하다.

전수감사(agent-platform-12, docs/FULL_AUDIT_2026-09-02.md §2-B) 발견 회귀 —
이전에는 `mfa_verified = user.mfa_enabled`로 "계정 설정"과 "이 세션이 최근
실제로 TOTP를 통과했다"를 혼동했다."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from src.api.foundation_deps import MFA_STEP_UP_WINDOW, get_tenant_context
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


def test_recent_totp_within_window_is_verified():
    user = _user(mfa_enabled=True, mfa_verified_at=datetime.now(timezone.utc))
    assert get_tenant_context(user).mfa_verified is True


def test_totp_older_than_step_up_window_is_not_verified():
    stale_at = datetime.now(timezone.utc) - MFA_STEP_UP_WINDOW - timedelta(seconds=1)
    user = _user(mfa_enabled=True, mfa_verified_at=stale_at)
    assert get_tenant_context(user).mfa_verified is False


def test_mfa_enabled_but_never_verified_is_not_verified():
    """마이그레이션 이전 계정(cdd905e63ffe) — mfa_enabled=True인데
    mfa_verified_at이 아직 NULL. fail-closed."""
    user = _user(mfa_enabled=True, mfa_verified_at=None)
    assert get_tenant_context(user).mfa_verified is False


def test_mfa_disabled_is_never_verified_even_with_a_recent_timestamp():
    """mfa_enabled=False면 애초에 검증할 대상이 없다 — timestamp가 있어도
    (예: 과거에 MFA를 껐다 켰다 한 흔적) 무시한다."""
    user = _user(mfa_enabled=False, mfa_verified_at=datetime.now(timezone.utc))
    assert get_tenant_context(user).mfa_verified is False
