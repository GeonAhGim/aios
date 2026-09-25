"""Request context ContextVar (8 fields) — propagated through middleware, event bus,
and background loops.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §3.1 PLT-01.

The context is propagated via a single `ContextVar` rather than function signatures (§1-1)
to avoid touching the signatures of 40+ existing services. `bind()` is the only place
that sets values; loggers, auditors, and metrics read only via `current()`.

`asyncio.create_task` clones and inherits the context at creation time (Python
contextvars default behavior), so child tasks created during HTTP request handling
automatically inherit the same trace_id. Conversely, code that runs independently
of the parent request (e.g., background loops) must explicitly create a new
context each tick via `bind_system()` — otherwise the context from the request
that last scheduled the task will leak in (§8 table "Request context" row).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.core.logging.request_context import request_id_var


class RequestContext(BaseModel):
    """§3.1 contract. `frozen=True` — create a new context via `bind()` to change values."""

    model_config = ConfigDict(frozen=True)

    trace_id: UUID
    request_id: str
    tenant_id: UUID | None = None
    actor_subject_id: UUID | Literal["system"] = "system"
    command_id: UUID | None = None
    component: str = "api.gateway"
    schema_version: Literal["v1"] = "v1"


_context_var: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def _fallback_context() -> RequestContext:
    """Default value when `current()` is called with nothing bound.

    Fail-open — observation defects do not block request processing (§8 "Middleware exception"
    row, same principle). If `request_id_var` already has a value (another session middleware
    set it first), inherit it so the request_id does not diverge across axes.
    """
    return RequestContext(
        trace_id=uuid.uuid4(),
        request_id=request_id_var.get() or uuid.uuid4().hex,
    )


def current() -> RequestContext:
    """Current context. Creates and returns a new one if nothing has been bound
    (does not persist — may be a different temporary value on each call, so safe
    for logging purposes only)."""
    ctx = _context_var.get()
    if ctx is None:
        return _fallback_context()
    return ctx


@contextmanager
def bind(**overrides: Any) -> Iterator[RequestContext]:
    """Binds a new context with `overrides` layered on top of the current one for this block.

    Also sets `request_id_var` to the same request_id so consumers of
    `get_current_request_id()` (e.g., `schema.py`) that predate PLT-01 continue
    to see a consistent value.
    """
    new_ctx = current().model_copy(update=overrides) if overrides else current()
    ctx_token = _context_var.set(new_ctx)
    request_id_token = request_id_var.set(new_ctx.request_id)
    try:
        yield new_ctx
    finally:
        request_id_var.reset(request_id_token)
        _context_var.reset(ctx_token)


@contextmanager
def bind_system(component: str) -> Iterator[RequestContext]:
    """For background loops — creates a system context with a new trace_id without
    inheriting the parent (request) context (actor_subject_id="system", tenant_id=None,
    command_id=None). Must be called every tick by the loop to prevent parent request
    context leakage."""
    with bind(
        trace_id=uuid.uuid4(),
        request_id=uuid.uuid4().hex,
        tenant_id=None,
        actor_subject_id="system",
        command_id=None,
        component=component,
    ) as ctx:
        yield ctx
