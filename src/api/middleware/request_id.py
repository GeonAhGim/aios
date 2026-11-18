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

import re
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.core.logging.request_context import request_id_var

REQUEST_ID_HEADER = "X-Request-ID"
_MAX_REQUEST_ID_LENGTH = 128

# ULID: 26 characters, base32 alphabet (excludes I, L, O, U to avoid confusion)
# Also accept UUID hex (32 hex chars) and any other alphanumeric/kebab string.
_VALID_REQUEST_ID_RE = re.compile(r"^[0-9a-zA-Z_-]+$")


def _is_valid_request_id(value: str) -> bool:
    """Return True when *value* is a well-formed correlation ID.

    Accepts:
    - UUID hex (32 hex chars)
    - ULID (26 base32 chars)
    - Any non-empty alphanumeric/kebab/underscore string up to 128 chars.

    Rejects anything that exceeds the length budget or contains characters
    that would break log-correlation (whitespace, control chars, slashes).
    """
    if not value or len(value) > _MAX_REQUEST_ID_LENGTH:
        return False
    return bool(_VALID_REQUEST_ID_RE.match(value))


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raw = request.headers.get(REQUEST_ID_HEADER)
        request_id = raw if raw and _is_valid_request_id(raw) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
