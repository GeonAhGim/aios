"""HTTP 진입점 rate limit 미들웨어.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-25

편차: 스펙 원문은 `RateLimitMiddleware(app, limiter, resolve_policy)`로
limiter를 생성자 인자로 받는 시그니처를 제시하지만, 그러면 미들웨어가 앱 조립
시점(`src.main` 모듈 임포트, 프로세스당 1회)에 구체 인스턴스를 캡처해버려
통합테스트가 `set_limiter(...)`로 격리할 방법이 없다(이미 등록된
`RequestContextMiddleware`가 `metrics()` 싱글턴을 생성자가 아니라 매 요청
`dispatch()`에서 조회하는 것과 같은 이유). 그래서 여기서도 `limiter()` 싱글턴
게터를 매 요청 조회한다 — `limiter/policy` 값 자체는 스펙과 동일하다.

등록 순서(main.py, §9 PLT-25 표): RateLimit → RequestContext → CORS — 이
미들웨어가 스택 가장 바깥이라, 거부된 요청은 trace_id 컨텍스트 바인딩·구조화
로그(RequestContextMiddleware)를 거치지 않는다. 폭주 상황에서 그 바인딩·로깅
비용조차 치르지 않고 최대한 빨리 거절하는 게 목적이라 의도적인 트레이드오프다
— 그래서 429 응답의 `X-Request-ID`/`X-Trace-Id`는 이 미들웨어가 직접 채운다.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

import jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from src.api.contracts.envelope import ApiError
from src.api.contracts.error_codes import HTTP_STATUS, ErrorCode
from src.api.middleware.request_context import TRACE_ID_HEADER
from src.api.middleware.request_id import REQUEST_ID_HEADER
from src.core.observability.metric_names import AUTH_RATE_LIMITED_COUNT_TOTAL
from src.core.observability.metrics import metrics
from src.core.rate_limit.limiter import limiter
from src.core.rate_limit.policy import POLICIES, RateLimitPolicy

logger = logging.getLogger(__name__)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def default_resolve_policy(request: Request) -> RateLimitPolicy | None:
    """경로/메서드 → 정책 매핑. `OPTIONS`(CORS preflight)는 제한하지 않는다."""
    path = request.url.path
    method = request.method.upper()
    if method == "POST" and path == "/auth/login":
        return POLICIES["auth_login"]
    if path.startswith("/admin"):
        return POLICIES["admin"]
    if path == "/metrics":
        return POLICIES["metrics"]
    if method in ("GET", "HEAD"):
        return POLICIES["read"]
    if method in ("POST", "PUT", "PATCH", "DELETE"):
        return POLICIES["mutation"]
    return None


def _resolve_key(request: Request, policy: RateLimitPolicy) -> str:
    if policy.key == "ip":
        return f"ip:{_client_ip(request)}"
    if policy.key == "tenant":
        tenant_id = request.headers.get("X-Tenant-Id")
        return f"tenant:{tenant_id}" if tenant_id else f"ip:{_client_ip(request)}"
    # "subject" — Authorization 헤더의 JWT를 서명 검증까지 마치고 sub만 쓴다.
    # (get_current_user와 같은 secret/algorithm) 검증 없이 sub를 신뢰하면
    # 공격자가 임의의 타인 user_id를 자처해 그 사람의 read/mutation 버킷을
    # 대신 소진시킬 수 있다(피해자 본인이 정작 429를 맞는 침묵형 DoS) — DB
    # 조회(get_user_by_id, 계정 상태 확인)까지는 하지 않는다. 버킷을 나누는
    # 용도일 뿐 인증 판정이 아니고, 그건 여전히 get_current_user 책임이다.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        secrets = request.app.state.secrets
        try:
            payload = jwt.decode(
                auth_header[7:],
                secrets.jwt_secret_key.get_secret_value(),
                algorithms=[secrets.jwt_algorithm],
            )
        except jwt.PyJWTError:
            payload = None
        if payload and payload.get("sub"):
            return f"user:{payload['sub']}"
    return f"ip:{_client_ip(request)}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(
        self,
        app: ASGIApp,
        resolve_policy: Callable[[Request], RateLimitPolicy | None] = default_resolve_policy,
    ) -> None:
        super().__init__(app)
        self._resolve_policy = resolve_policy

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        policy = self._resolve_policy(request)
        if policy is None:
            return await call_next(request)

        key = _resolve_key(request, policy)
        decision = await limiter().acquire(policy, key)
        if not decision.allowed:
            metrics().counter(AUTH_RATE_LIMITED_COUNT_TOTAL, {"policy": policy.name})
            request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
            trace_id = uuid.uuid4()
            logger.warning(
                "rate limit 초과: policy=%s key=%s",
                policy.name,
                key,
                extra={
                    "event": "rate_limit_exceeded",
                    "payload": {"policy": policy.name, "route": request.url.path},
                },
            )
            error = ApiError(
                error_code=ErrorCode.RATE_LIMIT_EXCEEDED.value,
                message="요청이 너무 많습니다. 잠시 후 다시 시도해주세요.",
                trace_id=trace_id,
                retry_after_seconds=decision.retry_after_s,
            )
            rejection = JSONResponse(
                status_code=HTTP_STATUS[ErrorCode.RATE_LIMIT_EXCEEDED],
                content=error.model_dump(mode="json"),
            )
            rejection.headers[REQUEST_ID_HEADER] = request_id
            rejection.headers[TRACE_ID_HEADER] = str(trace_id)
            rejection.headers["Retry-After"] = str(decision.retry_after_s)
            rejection.headers["RateLimit-Limit"] = str(policy.limit)
            rejection.headers["RateLimit-Remaining"] = "0"
            rejection.headers["RateLimit-Reset"] = str(decision.retry_after_s)
            return rejection

        response: Response = await call_next(request)
        response.headers["RateLimit-Limit"] = str(policy.limit)
        response.headers["RateLimit-Remaining"] = str(decision.remaining)
        response.headers["RateLimit-Reset"] = str(policy.window_seconds)
        return response
