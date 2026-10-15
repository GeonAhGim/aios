"""Contextvar holding the request correlation ID — placed in the core layer.

`src/api/middleware/request_id.py` sets this value, and the JSONLinesFormatter
in `schema.py` reads it. Since core referencing api would invert the layer
dependency (core is a lower level than api), the contextvar itself lives here
in core, with api middleware depending on it via import only.
"""

from __future__ import annotations

from contextvars import ContextVar

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_current_request_id() -> str | None:
    """Returns None when called outside the request context (e.g., background loop) —
    which itself is an honest signal that "this log does not belong to any HTTP request."""
    return request_id_var.get()
