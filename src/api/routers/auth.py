"""11.2/11.3 — 인증 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-11.1/FD-11.2, 16_backend_signatures.md

앱 조립 단계 — 이미 구현된 AuthService(11.2)/MfaService(11.3) 서비스
계층을 실제 HTTP로 노출한다. 로직은 여기서 새로 만들지 않는다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_auth_service, get_current_user, get_mfa_service, reauthenticate
from src.api.schemas.auth import (
    LoginRequest,
    MfaSetupRequest,
    MfaVerifyRequest,
    SignupRequest,
    TokenResponse,
)
from src.services.auth_service import AuthService, User
from src.services.mfa_service import MfaReauthenticationRequiredError, MfaService, MfaSetupResult

router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: SignupRequest, auth: AuthService = Depends(get_auth_service)
) -> ApiResponse[TokenResponse]:
    user = await auth.signup(body.email, body.password)
    return ok(TokenResponse(access_token=auth.issue_token(user)))


@router.post("/login")
async def login(
    body: LoginRequest, auth: AuthService = Depends(get_auth_service)
) -> ApiResponse[TokenResponse]:
    user = await auth.authenticate(body.email, body.password, totp_code=body.totp_code)
    return ok(TokenResponse(access_token=auth.issue_token(user)))


@router.post("/logout")
async def logout(user: User = Depends(get_current_user)) -> ApiResponse[dict[str, str]]:
    """Draft — stateless JWT라 서버측 무효화 메커니즘은 착수 시 확정 필요
    (16_backend_signatures.md 원문 그대로, 지금은 클라이언트가 토큰을
    버리는 것으로 충분하다고 가정)."""
    return ok({"status": "logged_out"})


@router.post("/mfa/setup")
async def setup_mfa(
    body: MfaSetupRequest | None = None,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
    mfa: MfaService = Depends(get_mfa_service),
) -> ApiResponse[MfaSetupResult]:
    body = body or MfaSetupRequest()
    if user.mfa_enabled:
        # 레드팀 감사 #11 — 이미 MFA가 켜진 계정이 다시 이 엔드포인트를
        # 호출하면 기존 secret을 조용히 덮어쓸 수 있었다(Bearer 토큰만
        # 있으면 비밀번호 없이도 공격자가 자신의 secret으로 재설정 가능).
        # 최초 설정(mfa_enabled=false)은 로그인 자체가 이미 증명이라
        # 재인증을 요구하지 않는다.
        if not body.password:
            raise MfaReauthenticationRequiredError(
                "이미 활성화된 MFA를 재설정하려면 비밀번호 재인증이 필요합니다."
            )
        await reauthenticate(auth, user, body.password, body.totp_code)
    result = await mfa.setup(user.user_id, user.email)
    return ok(result)


@router.post("/mfa/verify")
async def verify_mfa(
    body: MfaVerifyRequest,
    user: User = Depends(get_current_user),
    mfa: MfaService = Depends(get_mfa_service),
) -> ApiResponse[dict[str, bool]]:
    await mfa.verify(user.user_id, body.totp_code)
    return ok({"mfa_enabled": True})
