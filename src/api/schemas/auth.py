"""11.2/11.3 — Auth API request/response schemas.

Spec: 16_backend_signatures.md

Always converts the Service-layer User model through this Response
model rather than returning it directly (per 16_backend_signatures.md —
prevents divergence between the two layers).
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.services.auth_service import User


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class RefreshRequest(BaseModel):
    """PLT-24 — Why `session_id` must be sent alongside the refresh token:
    see `src/services/auth/refresh.py` module docstring — a refresh_hash alone
    cannot reverse-reference a rotated (compromised) token."""

    session_id: UUID
    refresh_token: str


class TokenResponse(BaseModel):
    """PLT-24 legacy `/auth/register` / `/auth/login` response contract
    (single non-rotating JWT) — both routes now return
    `src.services.auth.tokens.TokenPairResponse` (superset with
    backward-compatible fields: access_token/token_type kept +
    refresh_token/expires_in/session_id added), so this class is no longer
    used by routers. Deleting fields would be rejected by the P5 architecture
    guard as a contract break, so we keep the type for compatibility only
    (actual wiring is in routers/auth.py)."""

    access_token: str
    token_type: str = "bearer"


class MfaVerifyRequest(BaseModel):
    totp_code: str


class MfaSetupRequest(BaseModel):
    """Initial setup (mfa_enabled=false) is allowed without re-authentication —
    the login itself already serves as proof of identity. password is only
    required when reconfiguring (reissuing) an already-enabled MFA
    (Red Team audit #11 — prevents an attacker who stole a Bearer token from
    overwriting the existing secret and hijacking 2FA)."""

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
