"""108 §2 structured logging required field set — single source of truth.

Spec: docs/design/codex/108_structured_logging_and_observability_field_standard_v1.0.md §2,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-02.

`REQUIRED_FIELDS` is the sole definition listing the 8 fields from 108 §2 table — other
modules (schema.py for PLT-03, metric/notification validators, etc.) import this constant
to compare against and never hardcode the set again. `StructuredLogLine` is a Pydantic model
that adds non-108 fields (timestamp/message/extra) needed for log line rendering on top of
these 8 fields.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.observability.context import RequestContext

Level = Literal["debug", "info", "warn", "error"]

# 108 §2 table order as-is — trace_id, tenant_id, actor_subject_id, command_id (or
# query_id), component, event, level, duration_ms. "critical" is not used as a log level
# (§2 table `level` row) — CRITICAL events are recorded as level="error" + event="*_critical".
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
    """108 §2 fields + timestamp/message/extra needed for log line rendering.

    Field names and types must match the §2 table exactly (`test_fields.py` verifies that
    the field set of `REQUIRED_FIELDS` and this model match precisely — adding or omitting
    any field causes failure).

    `extra="forbid"`: pydantic's default (`extra="ignore"`) would silently drop a
    caller's typo'd field (e.g. `trace__id`) into `.model_extra`, leaving `.trace_id`
    to fail validation or fall back to a default with no trace of the real cause —
    the 108 §2 8-field set is the contract, so any key outside it must surface as a
    `ValidationError` immediately.
    """

    model_config = ConfigDict(extra="forbid")

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

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_tz_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be a tz-aware UTC datetime, got a naive datetime")
        return value


def from_record(record: logging.LogRecord, ctx: RequestContext) -> StructuredLogLine:
    """Construct `StructuredLogLine` from `LogRecord` + current `RequestContext`.

    `event`/`duration_ms`/`payload` default to `log.unstructured`/`None`/`{}` if the caller
    did not explicitly pass them via `logging.info(msg, extra={...})`.
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
