"""HTTP entry-point request context binding middleware — superset of request_id.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-05.

Inherits from `RequestIdMiddleware` (task-107, `X-Request-ID` round-trip) and keeps
that contract intact, then binds the full `RequestContext` (8 fields) on top. When
a `traceparent` (W3C trace-context) header is present, it adopts the trace-id to
chain the trace_id with upstream proxy/APM; otherwise a new one is generated.

At request end, emits one `event=http_request_completed` log line (108 §2 8 fields
+ duration_ms/route/status) and `aios.api.request.*` metrics. Only this middleware
is registered in `main.py` — registering the parent `RequestIdMiddleware` separately
would set `X-Request-ID` twice (harmless but unnecessary, see §2.1 table).
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
    """Extract trace-id from the W3C `traceparent` header. Generates a new trace_id
    when the format is invalid or all-zero (invalid value per spec) — do not trust
    just because the client sent the header."""
    if traceparent:
        match = _TRACEPARENT_RE.match(traceparent)
        if match and match.group(1) != "0" * 32:
            return uuid.UUID(hex=match.group(1))
    return uuid.uuid4()


def _route_template(request: Request) -> str:
    """Route template for labels/logging (e.g. `/executions/{execution_id}/start`) —
    using the actual request.url.path with concrete values would cause unbounded
    metric cardinality growth (§3.2 label cardinality cap). Falls back to the
    original path when no matching route is found (404)."""
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
