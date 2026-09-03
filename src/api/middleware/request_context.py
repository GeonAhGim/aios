"""HTTP 진입점 요청 컨텍스트 바인딩 미들웨어 — request_id의 상위 집합.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-05.

`RequestIdMiddleware`(task-107, `X-Request-ID` 왕복)를 상속해 그 계약은 그대로
두고, 그 위에 `RequestContext`(8필드) 전체를 바인딩한다. `traceparent`(W3C
trace-context) 헤더가 있으면 그 trace-id를 채택해 업스트림 프록시/APM과
trace_id가 이어지게 하고, 없거나 형식이 아니면 새로 생성한다.

요청 종료 시 `event=http_request_completed` 로그 1줄(108 §2 8필드 + duration_ms/
route/status)과 `aios.api.request.*` 메트릭을 남긴다. `main.py`는 이 미들웨어만
등록한다 — 부모 `RequestIdMiddleware`를 별도로 또 등록하면 `X-Request-ID`를 두 번
set하게 된다(무해하지만 불필요, §2.1 표 참조).
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Match

from src.api.middleware.request_id import REQUEST_ID_HEADER, RequestIdMiddleware
from src.core.observability.context import bind
from src.core.observability.metric_names import (
    API_REQUEST_COUNT_TOTAL,
    API_REQUEST_DURATION_SECONDS,
)
from src.core.observability.metrics import metrics

logger = logging.getLogger(__name__)

TRACE_ID_HEADER = "X-Trace-Id"
_TRACEPARENT_HEADER = "traceparent"
_TRACEPARENT_RE = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")


def _extract_trace_id(traceparent: str | None) -> uuid.UUID:
    """W3C `traceparent` 헤더에서 trace-id를 뽑는다. 형식이 아니거나 all-zero
    (스펙상 무효값)면 새 trace_id를 생성한다 — 클라이언트가 헤더를 보냈다는
    이유만으로 신뢰하지 않는다."""
    if traceparent:
        match = _TRACEPARENT_RE.match(traceparent)
        if match and match.group(1) != "0" * 32:
            return uuid.UUID(hex=match.group(1))
    return uuid.uuid4()


def _route_template(request: Request) -> str:
    """라벨/로그용 경로 템플릿(예: `/executions/{execution_id}/start`) — 실제
    값이 섞인 `request.url.path`를 그대로 쓰면 메트릭 카디널리티가 무한히
    늘어난다(§3.2 라벨 카디널리티 상한). 매칭되는 라우트가 없으면(404) 원본
    경로로 폴백한다."""
    for route in request.app.routes:
        match, _child_scope = route.matches(request.scope)
        if match == Match.FULL:
            return getattr(route, "path", request.url.path)
    return request.url.path


class RequestContextMiddleware(RequestIdMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        trace_id = _extract_trace_id(request.headers.get(_TRACEPARENT_HEADER))
        route = _route_template(request)
        start = time.monotonic()
        status_code = 500
        response: Response | None = None
        with bind(trace_id=trace_id, request_id=request_id, component="api.gateway"):
            try:
                response = await call_next(request)
                status_code = response.status_code
            finally:
                duration_ms = round((time.monotonic() - start) * 1000)
                logger.info(
                    "%s %s %s",
                    request.method,
                    request.url.path,
                    status_code,
                    extra={
                        "event": "http_request_completed",
                        "duration_ms": duration_ms,
                        "payload": {"route": route, "status": status_code},
                    },
                )
                labels = {"route": route, "method": request.method, "status": str(status_code)}
                metrics().counter(API_REQUEST_COUNT_TOTAL, labels)
                metrics().observe(API_REQUEST_DURATION_SECONDS, duration_ms / 1000, labels)
        assert response is not None
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers[TRACE_ID_HEADER] = str(trace_id)
        return response
