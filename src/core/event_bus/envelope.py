"""PLT-06 — Event Bus Envelope (EventEnvelope).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-06

Unlike `asyncio.create_task` which inherits context at creation time,
the event bus has an `asyncio.Queue` between `publish()` and actual
handler execution, so contextvars do not propagate automatically
(queue consumer workers run at a different time and in a different
context than the publish call). Therefore the `RequestContext` at
publish time is explicitly packed into the envelope to carry it
across the queue.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.core.observability.context import current


class EventEnvelope(BaseModel):
    """PLT-06 contract. `frozen=True` — immutable snapshot including payload."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    event_id: UUID
    topic: str
    trace_id: UUID
    tenant_id: UUID | None
    actor_subject_id: UUID | Literal["system"]
    occurred_at: datetime
    schema_version: Literal["v1"] = "v1"
    payload: Any


def wrap(topic: str, payload: Any) -> EventEnvelope:
    """Create an envelope with the current `RequestContext`(PLT-01) at publish time."""
    ctx = current()
    return EventEnvelope(
        event_id=uuid.uuid4(),
        topic=topic,
        trace_id=ctx.trace_id,
        tenant_id=ctx.tenant_id,
        actor_subject_id=ctx.actor_subject_id,
        occurred_at=datetime.now(timezone.utc),
        payload=payload,
    )


def unwrap(obj: Any) -> tuple[EventEnvelope | None, Any]:
    """If envelope return `(envelope, payload)`, else `(None, obj)` — migration compatible."""
    if isinstance(obj, EventEnvelope):
        return obj, obj.payload
    return None, obj
