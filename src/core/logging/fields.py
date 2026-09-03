"""108 §2 구조화 로그 필수 필드 집합 — 단일 출처.

Spec: docs/design/codex/108_structured_logging_and_observability_field_standard_v1.0.md §2,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-02.

`REQUIRED_FIELDS`가 108 §2 표의 8필드를 나열하는 유일한 정의다 — 다른 모듈(PLT-03의
schema.py, 메트릭/알림 검증 등)은 이 상수를 import해서 비교하고, 여기서 다시
하드코딩하지 않는다. `StructuredLogLine`은 이 8필드에 로그 라인 자체에 필요한
비-108 필드(timestamp/message/extra)를 더한 pydantic 모델이다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Final, Literal

from pydantic import BaseModel, Field

from src.core.observability.context import RequestContext

Level = Literal["debug", "info", "warn", "error"]

# 108 §2 표 순서 그대로 — trace_id, tenant_id, actor_subject_id, command_id(또는
# query_id), component, event, level, duration_ms. `critical`은 로그 레벨로 쓰지
# 않는다(§2 표 `level` 행) — CRITICAL 이벤트는 level="error" + event="*_critical"로 남긴다.
REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "trace_id",
    "tenant_id",
    "actor_subject_id",
    "command_id",
    "component",
    "event",
    "level",
    "duration_ms",
)

_LEVEL_MAP: Final[dict[str, Level]] = {
    "DEBUG": "debug",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}


class StructuredLogLine(BaseModel):
    """108 §2 필드 + 로그 라인 렌더링에 필요한 timestamp/message/extra.

    필드 이름·타입은 §2 표와 동일해야 한다(`test_fields.py`가 `REQUIRED_FIELDS`와
    이 모델의 필드 집합이 정확히 일치하는지 — 추가·누락 모두 실패하도록 — 검증한다).
    """

    timestamp: datetime
    level: Level
    trace_id: str
    tenant_id: str | None = None
    actor_subject_id: str
    command_id: str | None = None
    component: str
    event: str
    duration_ms: int | None = None
    message: str
    extra: dict[str, Any] = Field(default_factory=dict)


def from_record(record: logging.LogRecord, ctx: RequestContext) -> StructuredLogLine:
    """`LogRecord` + 현재 `RequestContext`로부터 `StructuredLogLine`을 만든다.

    `event`/`duration_ms`/`payload`는 호출자가 `logging.info(msg, extra={...})`로
    명시하지 않으면 각각 기본값(`log.unstructured`/`None`/`{}`)으로 채워진다.
    """
    raw_duration = getattr(record, "duration_ms", None)
    return StructuredLogLine(
        timestamp=datetime.fromtimestamp(record.created, tz=timezone.utc),
        level=_LEVEL_MAP.get(record.levelname, "info"),
        trace_id=str(ctx.trace_id),
        tenant_id=str(ctx.tenant_id) if ctx.tenant_id is not None else None,
        actor_subject_id=str(ctx.actor_subject_id),
        command_id=str(ctx.command_id) if ctx.command_id is not None else None,
        component=ctx.component,
        event=getattr(record, "event", "log.unstructured"),
        duration_ms=round(raw_duration) if raw_duration is not None else None,
        message=record.getMessage(),
        extra=getattr(record, "payload", {}),
    )
