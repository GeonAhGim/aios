"""11.2/11.3 — 인증 API 요청/응답 스키마.

Spec: 16_backend_signatures.md

Service 계층의 User 모델을 그대로 반환하지 않고 항상 이 Response
모델로 변환한다(16_backend_signatures.md 원칙 — 두 계층이 어긋날
위험을 방지).
"""
from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field

from src.services.auth_service import User


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MfaVerifyRequest(BaseModel):
    totp_code: str


class MfaSetupRequest(BaseModel):
    """최초 설정(mfa_enabled=false)은 재인증 없이 그대로 허용한다 — 로그인
    자체가 이미 인증 증명이다. 이미 켜진 MFA를 다시 설정(재발급)하는
    경우에만 password가 필요하다(레드팀 감사 #11 — Bearer 토큰 탈취만으로
    기존 secret을 덮어써 2FA를 탈취하는 경로를 막는다)."""

    password: str | None = None
    totp_code: str | None = None


class UserResponse(BaseModel):
    user_id: str
    email: str
    display_name: str | None
    mfa_enabled: bool
    status: str
    is_verifier: bool
    is_platform_admin: bool


def to_user_response(user: User) -> UserResponse:
    return UserResponse(
        user_id=str(user.user_id),
        email=user.email,
        display_name=user.display_name,
        mfa_enabled=user.mfa_enabled,
        status=user.status,
        is_verifier=user.is_verifier,
        is_platform_admin=user.is_platform_admin,
    )
