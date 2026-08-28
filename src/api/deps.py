"""16번대 — FastAPI 공용 의존성.

get_pool()은 app.state.pool을 그대로 반환한다 — 앱 조립 단계(main.py
lifespan)가 채워둔 것을 재사용하며, 각 서비스 생성자가 요구하는
asyncpg.Pool 타입 그대로 넘긴다(SQLAlchemy 세션이 아님 — main.py 상단
편차 설명 참조).

get_current_user()는 Authorization: Bearer <JWT> 헤더를 검증해 사용자를
반환한다. get_current_verifier/get_current_admin은 15번 문서 §15.6
RBAC(is_verifier/is_platform_admin 플래그)를 그대로 강제한다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer

from src.services.auth_service import AuthService, User, get_user_by_id
from src.services.mfa_service import MfaService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


async def get_current_user(
    request: Request,
    token: str | None = Depends(oauth2_scheme),
    pool: asyncpg.Pool = Depends(get_pool),
) -> User:
    if token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "인증이 필요합니다.")

    secrets = request.app.state.secrets
    try:
        payload = jwt.decode(
            token, secrets.jwt_secret_key, algorithms=[secrets.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "유효하지 않은 토큰입니다.") from exc

    user = await get_user_by_id(pool, UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "존재하지 않는 사용자입니다.")
    return user


async def get_current_verifier(user: User = Depends(get_current_user)) -> User:
    if not user.is_verifier:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "검증담당자 권한이 필요합니다.")
    return user


async def get_current_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_platform_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "운영자 권한이 필요합니다.")
    return user


def get_mfa_service(
    request: Request, pool: asyncpg.Pool = Depends(get_pool)
) -> MfaService:
    secrets = request.app.state.secrets
    return MfaService(pool, encryption_key=secrets.credential_encryption_key)


def get_auth_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    mfa: MfaService = Depends(get_mfa_service),
) -> AuthService:
    secrets = request.app.state.secrets
    return AuthService(
        pool,
        jwt_secret_key=secrets.jwt_secret_key,
        jwt_algorithm=secrets.jwt_algorithm,
        jwt_expire_minutes=secrets.jwt_expire_minutes,
        verify_totp=mfa.verify_totp_for_login,
    )
