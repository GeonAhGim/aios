"""F3(AUDIT_2026-09-30 §auth_rls) — `get_current_user`(deps.py)가 서명이 유효한
access JWT라도 claims(sub/tid/auth_level)가 `sid`가 가리키는 세션의 실제
소유자와 일치하는지 대조하는지 검증한다.

Spec: docs/audits/AUDIT_2026-09-30_auth_rls.md F3(M), src/api/deps.py:78-99,
src/services/auth/session_repository.py(verify_principal_binding).

감사 재현: A 세션의 sid + B의 sub/tid로 서명한 "혼합" 토큰은 서명 자체는
유효하다(서명 키 없는 위조가 아니다) — `get_active()`가 sid 활성 여부만
확인하던 기존 코드는 이를 B 사용자로 수용했다. 이 테스트는 그 재현이
거부로 바뀌었는지, 그리고 정상 로그인/세션 흐름은 회귀 없이 통과하는지
확인한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.api.contracts.exception_mapping import SessionRevokedError
from src.api.deps import get_current_user
from src.services.auth import session_repository as sessions
from src.services.auth.tokens import TokenIssuer, TokenVerifier
from tests.integration.conftest import create_test_user

_JWT_ENV = {"JWT_SIGNING_KEYS": "test1:" + "aa" * 32, "JWT_ACTIVE_KID": "test1"}


def _issuer() -> TokenIssuer:
    return TokenIssuer.from_env(env=_JWT_ENV)


def _verifier() -> TokenVerifier:
    return TokenVerifier.from_env(env=_JWT_ENV)


async def _insert_active_session(pool, user_id):
    _plaintext, refresh_hash = TokenIssuer.issue_refresh()
    async with pool.acquire() as conn:
        return await sessions.insert_session(
            conn,
            user_id=user_id,
            tenant_id=user_id,
            refresh_hash=refresh_hash,
            ip_hash=None,
            ua_hash=None,
            expires_at=datetime.now(timezone.utc) + timedelta(days=14),
        )


async def test_mixed_session_and_claims_from_different_users_rejected(pool):
    """감사 재현: A의 활성 세션 sid + B의 sub/tid로 서명한 access 토큰은
    서명은 유효하지만 세션 소유자와 일치하지 않으므로 거부돼야 한다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    session_a = await _insert_active_session(pool, user_a)

    issuer = _issuer()
    mixed_token = issuer.issue_access(
        user_id=user_b,
        tenant_id=user_b,
        session_id=session_a.id,
        auth_level="PASSWORD",
    )

    with pytest.raises(SessionRevokedError):
        await get_current_user(token=mixed_token, pool=pool, verifier=_verifier())


async def test_mixed_auth_level_claim_rejected(pool):
    """auth_level만 세션과 달라도(MFA_VERIFIED로 상향 위조) 거부돼야 한다 —
    sub/tid만 대조하고 auth_level을 빠뜨리면 인증수준 상승 우회가 남는다."""
    user_a = await create_test_user(pool)
    session_a = await _insert_active_session(pool, user_a)
    assert session_a.auth_level == "PASSWORD"

    issuer = _issuer()
    escalated_token = issuer.issue_access(
        user_id=user_a,
        tenant_id=user_a,
        session_id=session_a.id,
        auth_level="MFA_VERIFIED",
    )

    with pytest.raises(SessionRevokedError):
        await get_current_user(token=escalated_token, pool=pool, verifier=_verifier())


async def test_legitimate_session_and_claims_from_same_user_accepted(pool):
    """회귀 없음: 정당한 로그인/refresh 흐름처럼 세션 자신의 소유자
    claims(sub/tid/auth_level 모두 세션과 일치)로 서명된 토큰은 그대로
    허용돼야 한다(pre-PLT-26 tenant_id == user_id 관례 포함)."""
    user_id = await create_test_user(pool)
    session = await _insert_active_session(pool, user_id)

    issuer = _issuer()
    legit_token = issuer.issue_access(
        user_id=session.user_id,
        tenant_id=session.tenant_id,
        session_id=session.id,
        auth_level=session.auth_level,
    )

    authed = await get_current_user(token=legit_token, pool=pool, verifier=_verifier())

    assert authed.user_id == user_id
    assert authed.session_id == session.id
    assert authed.auth_level == "PASSWORD"


async def test_revoked_session_still_rejected_before_principal_check(pool):
    """실패주입 아님, 순서 회귀 확인: revoke된 세션은 principal 대조 전
    `get_active()` 단계에서 이미 None이 되어 동일한 SessionRevokedError로
    거부돼야 한다(새 대조 로직이 기존 revoke 검사를 우회하지 않음)."""
    user_id = await create_test_user(pool)
    session = await _insert_active_session(pool, user_id)
    async with pool.acquire() as conn:
        await sessions.revoke(conn, session.id, reason="logout")

    issuer = _issuer()
    token = issuer.issue_access(
        user_id=session.user_id,
        tenant_id=session.tenant_id,
        session_id=session.id,
        auth_level=session.auth_level,
    )

    with pytest.raises(SessionRevokedError):
        await get_current_user(token=token, pool=pool, verifier=_verifier())
