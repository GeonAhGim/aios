"""11.2/11.3 + PLT-24 — Auth API router.

Spec: 기능설계문서_v1.20.md#FD-11.1/FD-11.2, 16_backend_signatures.md,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§3.4, §9 PLT-24

App-assembly layer — exposes the already-implemented AuthService(11.2)/
MfaService(11.3) and the `src/services/auth/{login,refresh,logout}.py`
(PLT-24) use cases over real HTTP. No new logic is created here — failure
paths also raise the domain exception as-is and let `exception_mapping.py`
map it (§3.3 "no raw HTTPException in new code"; `test_no_raw_http_exception.py`
already enforces this file as a check target).

`/register` also reuses `login.issue_token_pair()` to issue a session+token
pair — immediately after signup the token must still be one that
`get_current_user()` (based on PLT-23 TokenVerifier + active-session check)
can authenticate, so the legacy `AuthService.issue_token()` (a single
non-rotating JWT) can no longer keep the user logged in.
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
        # Red-team audit #11 — if an account that already has MFA enabled
        # called this endpoint again, it could silently overwrite the
        # existing secret (an attacker holding only the Bearer token could
        # reset it to their own secret without the password). Initial setup
        # (mfa_enabled=false) does not require reauthentication because
        # login itself is already proof.
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
