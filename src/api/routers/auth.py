"""11.2/11.3 — 인증 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-11.1/FD-11.2, 16_backend_signatures.md

앱 조립 단계 — 이미 구현된 AuthService(11.2)/MfaService(11.3) 서비스
계층을 실제 HTTP로 노출한다. 로직은 여기서 새로 만들지 않는다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_auth_service, get_current_user, get_mfa_service
from src.api.schemas.auth import (
    LoginRequest,
    MfaVerifyRequest,
    SignupRequest,
    TokenResponse,
)
from src.services.auth_service import AuthError, AuthService, User
from src.services.mfa_service import MfaError, MfaService, MfaSetupResult

router = APIRouter()


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: SignupRequest, auth: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    try:
        user = await auth.signup(body.email, body.password)
    except AuthError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return TokenResponse(access_token=auth.issue_token(user))


@router.post("/login")
async def login(
    body: LoginRequest, auth: AuthService = Depends(get_auth_service)
) -> TokenResponse:
    try:
        user = await auth.authenticate(body.email, body.password, totp_code=body.totp_code)
    except AuthError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    return TokenResponse(access_token=auth.issue_token(user))


@router.post("/logout")
async def logout(user: User = Depends(get_current_user)) -> dict[str, str]:
    """Draft — stateless JWT라 서버측 무효화 메커니즘은 착수 시 확정 필요
    (16_backend_signatures.md 원문 그대로, 지금은 클라이언트가 토큰을
    버리는 것으로 충분하다고 가정)."""
    return {"status": "logged_out"}


@router.post("/mfa/setup")
async def setup_mfa(
    user: User = Depends(get_current_user), mfa: MfaService = Depends(get_mfa_service)
) -> MfaSetupResult:
    return await mfa.setup(user.user_id, user.email)


@router.post("/mfa/verify")
async def verify_mfa(
    body: MfaVerifyRequest,
    user: User = Depends(get_current_user),
    mfa: MfaService = Depends(get_mfa_service),
) -> dict[str, bool]:
    try:
        await mfa.verify(user.user_id, body.totp_code)
    except MfaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return {"mfa_enabled": True}
