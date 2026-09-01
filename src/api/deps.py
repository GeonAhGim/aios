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

from src.core.event_bus.bus import EventBus
from src.services.auth_service import AuthError, AuthService, User, get_user_by_id
from src.services.mfa_service import MfaService

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


async def get_pool(request: Request) -> asyncpg.Pool:
    pool: asyncpg.Pool = request.app.state.pool
    return pool


async def get_event_bus(request: Request) -> EventBus:
    """앱 조립 단계(main.py lifespan)가 채워둔 InProcessEventBus를 그대로
    반환한다 — 테스트는 이 의존성을 dependency_overrides로 교체해 실제
    발송기(SMTP/FCM) 부재로 인한 CRITICAL 재시도 지연(최대 31초, §5.5)을
    피한다."""
    event_bus: EventBus = request.app.state.event_bus
    return event_bus


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
            token, secrets.jwt_secret_key.get_secret_value(), algorithms=[secrets.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "유효하지 않은 토큰입니다.") from exc

    user = await get_user_by_id(pool, UUID(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "존재하지 않는 사용자입니다.")
    if user.status in ("SUSPENDED", "DELETED"):
        # AuthService.authenticate()는 로그인 시점에 SUSPENDED/DELETED를
        # 거부하지만, 발급된 JWT는 만료 전까지(기본 60분) 그 자체로 유효해
        # 매 요청마다 이 검사가 없으면 정지 이후에도 기존 토큰으로 계속
        # API를 쓸 수 있었다(라우터 조립 중 발견, 로그인 시점 검사만으로는
        # 충분하지 않다는 것을 실증).
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "정지되었거나 삭제된 계정입니다.")
    return user


async def reauthenticate(
    auth: AuthService, user: User, password: str, totp_code: str | None = None
) -> None:
    """비밀번호(+MFA 활성 시 TOTP)를 다시 확인한다 — 이미 로그인된 세션이라도
    자금 이동/보안설정 변경처럼 민감한 액션 앞에서는 Bearer 토큰 탈취만으로
    통과할 수 없게 막는다. AuthService.authenticate()를 그대로 재사용한다
    (로그인 가능 = 재인증 성공, 새 검증 로직을 만들지 않는다). users.py의
    화이트리스트/탈퇴 라우터와 동일한 원칙을 공유 지점으로 뺀 것."""
    try:
        await auth.authenticate(user.email, password, totp_code=totp_code)
    except AuthError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "재인증에 실패했습니다.") from exc


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
    return MfaService(pool, encryption_key=secrets.credential_encryption_key.get_secret_value())


def get_auth_service(
    request: Request,
    pool: asyncpg.Pool = Depends(get_pool),
    mfa: MfaService = Depends(get_mfa_service),
) -> AuthService:
    secrets = request.app.state.secrets
    return AuthService(
        pool,
        jwt_secret_key=secrets.jwt_secret_key.get_secret_value(),
        jwt_algorithm=secrets.jwt_algorithm,
        jwt_expire_minutes=secrets.jwt_expire_minutes,
        verify_totp=mfa.verify_totp_for_login,
    )
