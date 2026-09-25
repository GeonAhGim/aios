"""HTTP entry-point rate limit middleware.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-25

Deviation: The spec describes `RateLimitMiddleware(app, limiter, resolve_policy)`
with `limiter` as a constructor argument, but that would pin a concrete instance
at app assembly time (`src.main` module import, once per process), leaving no way
for integration tests to isolate via `set_limiter(...)` (same reason
`RequestContextMiddleware` looks up the `metrics()` singleton per-request in
`dispatch()` rather than in `__init__`). So this middleware also resolves the
`limiter()` singleton getter per request — the `limiter/policy` values themselves
match the spec exactly.

Registration order (main.py, §9 PLT-25 table): RateLimit → RequestContext → CORS.
Because this middleware sits on the outside of the stack, rejected requests skip
trace_id context binding and structured logging (RequestContextMiddleware). The
intentional trade-off is to reject as fast as possible, even skipping the binding
and logging overhead, especially during traffic spikes. Therefore this middleware
itself populates `X-Request-ID`/`X-Trace-Id` on 429 responses.
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
    """Route/method → policy mapping. Does not limit `OPTIONS` (CORS preflight)."""
    path = request.url.path
    method = request.method.upper()
    if method == "POST" and path == "/auth/login":
        return POLICIES["auth_login"]
    if path.startswith("/admin"):
        return POLICIES["admin"]
    if path == "/metrics":
        return POLICIES["metrics"]
    if path.startswith("/v1/assistant"):
        return POLICIES["ai_assistant"]
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
    # "subject" — Decode the JWT from the Authorization header and use only `sub`
    # after signature verification (same secret/algorithm as get_current_user).
    # Trusting `sub` without verification would let an attacker impersonate any
    # user_id, exhausting that user's read/mutation bucket on their behalf
    # (silent DoS where the real victim gets 429). We do not perform a DB lookup
    # (get_user_by_id, account status check). This is only for bucket separation,
    # not authentication — that remains get_current_user's responsibility.
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
                "rate limit exceeded: policy=%s key=%s",
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
