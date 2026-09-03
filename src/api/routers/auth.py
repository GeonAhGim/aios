"""11.2/11.3 + PLT-24 — 인증 API 라우터.

Spec: 기능설계문서_v1.20.md#FD-11.1/FD-11.2, 16_backend_signatures.md,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.4, §9 PLT-24

앱 조립 단계 — 이미 구현된 AuthService(11.2)/MfaService(11.3)와
`src/services/auth/{login,refresh,logout}.py`(PLT-24) 유스케이스를
실제 HTTP로 노출한다. 로직은 여기서 새로 만들지 않는다 — 실패 경로도
도메인 예외를 그대로 raise해 `exception_mapping.py`가 매핑하게 둔다
(§3.3 "신규 코드에서 raw HTTPException 금지", `test_no_raw_http_exception.py`
가 이 파일을 이미 검사 대상으로 강제한다).

`/register`도 `login.issue_token_pair()`를 재사용해 세션+토큰 쌍을
발급한다 — 가입 직후에도 `get_current_user()`(PLT-23 TokenVerifier +
세션 활성 확인 기반)로 인증되는 토큰이어야 하므로, 레거시
`AuthService.issue_token()`(단일 비회전 JWT)로는 더 이상 로그인 상태를
유지할 수 없다.
"""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, Request, status

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import (
    AuthenticatedUser,
    get_auth_service,
    get_current_user,
    get_mfa_service,
    get_pool,
    get_token_issuer,
    reauthenticate,
)
from src.api.schemas.auth import (
    LoginRequest,
    MfaSetupRequest,
    MfaVerifyRequest,
    RefreshRequest,
    SignupRequest,
)
from src.services.auth import login as login_usecase
from src.services.auth import logout as logout_usecase
from src.services.auth import refresh as refresh_usecase
from src.services.auth.tokens import ClientInfo, TokenIssuer, TokenPairResponse
from src.services.auth_service import AuthService, User
from src.services.mfa_service import MfaReauthenticationRequiredError, MfaService, MfaSetupResult

router = APIRouter()


def _client_info(request: Request) -> ClientInfo:
    return ClientInfo(
        ip=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: SignupRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    pool: asyncpg.Pool = Depends(get_pool),
    issuer: TokenIssuer = Depends(get_token_issuer),
) -> ApiResponse[TokenPairResponse]:
    user = await auth.signup(body.email, body.password)
    pair = await login_usecase.issue_token_pair(pool, issuer, user, client=_client_info(request))
    return ok(pair)


@router.post("/login")
async def login(
    body: LoginRequest,
    request: Request,
    auth: AuthService = Depends(get_auth_service),
    pool: asyncpg.Pool = Depends(get_pool),
    issuer: TokenIssuer = Depends(get_token_issuer),
) -> ApiResponse[TokenPairResponse]:
    pair = await login_usecase.login(
        pool,
        auth,
        issuer,
        email=body.email,
        password=body.password,
        totp_code=body.totp_code,
        client=_client_info(request),
    )
    return ok(pair)


@router.post("/refresh")
async def refresh(
    body: RefreshRequest,
    pool: asyncpg.Pool = Depends(get_pool),
    issuer: TokenIssuer = Depends(get_token_issuer),
) -> ApiResponse[TokenPairResponse]:
    pair = await refresh_usecase.refresh(
        pool, issuer, session_id=body.session_id, refresh_token=body.refresh_token
    )
    return ok(pair)


@router.post("/logout")
async def logout(
    user: AuthenticatedUser = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[dict[str, str]]:
    await logout_usecase.logout(pool, session_id=user.session_id, user_id=user.user_id)
    return ok({"status": "logged_out"})


@router.post("/logout-all")
async def logout_all(
    user: AuthenticatedUser = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_pool),
) -> ApiResponse[dict[str, int]]:
    revoked_count = await logout_usecase.logout_all(pool, user_id=user.user_id)
    return ok({"revoked_count": revoked_count})


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
