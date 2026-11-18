"""Request correlation ID middleware — ties the entire processing path of a
single request (router → service → log) to one ID.

Behaviour: if the incoming request carries an `X-Request-ID` header, use it
as-is; otherwise generate a new one. Store it in a contextvar
(core/logging/request_context.py — see that module's docstring for why it
lives in the core layer) during request handling, and let
JSONLinesFormatter automatically fill it in as the default whenever the
caller does not supply a correlation_id for a log line. Echo the same value
back in the `X-Request-ID` response header so the client can locate server
logs with that single ID when filing an incident report.

Registration via `app.add_middleware(RequestIdMiddleware)` happens at the
app assembly point in main.py (this file provides only the middleware itself).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging.request_context import request_id_var
from src.core.logging.request_id_format import is_valid_request_id

REQUEST_ID_HEADER = "X-Request-ID"


def _accept_or_new(raw: str | None) -> str:
    """Return the client id when it is a valid UUID/ULID (<=128 chars); otherwise a fresh
    32-hex id, so malformed or oversized ids are never echoed or logged."""
    if raw and is_valid_request_id(raw):
        return raw
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = _accept_or_new(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
