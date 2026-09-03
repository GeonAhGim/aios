"""PLT-06 — Event Bus 봉투(EventEnvelope).

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A) PLT-06

`asyncio.create_task`가 생성 시점의 컨텍스트를 자동 상속하는 것과 달리,
이벤트 버스는 `publish()`와 실제 핸들러 실행 사이에 `asyncio.Queue`가
끼어 있어 contextvars가 자동으로 이어지지 않는다(큐 소비자 워커 코루틴은
publish 호출과 다른 시점·다른 컨텍스트에서 돈다). 그래서 publish 시점의
PLT-01 `RequestContext`를 봉투에 명시적으로 실어 큐를 건너 나른다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.core.observability.context import current


class EventEnvelope(BaseModel):
    """PLT-06 계약. `frozen=True` — payload 포함 불변 스냅샷."""

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
    """publish 시점의 현재 `RequestContext`(PLT-01)로 봉투를 만든다."""
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
    """봉투면 `(envelope, payload)`, 아니면 `(None, obj)` — 전환기 호환."""
    if isinstance(obj, EventEnvelope):
        return obj, obj.payload
    return None, obj
