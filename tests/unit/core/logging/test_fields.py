import logging
import uuid

import pytest
from pydantic import ValidationError

from src.core.logging.fields import REQUIRED_FIELDS, StructuredLogLine, from_record
from src.core.observability.context import bind, bind_system

# 108 §2 표(docs/design/codex/108_..._v1.0.md#§2)를 그대로 옮긴 리터럴 — fields.py의
# REQUIRED_FIELDS가 여기서 어긋나면(추가·누락 모두) 이 테스트가 즉시 실패해야 한다.
_SPEC_108_S2_FIELDS = frozenset(
    {
        "trace_id",
        "tenant_id",
        "actor_subject_id",
        "command_id",
        "component",
        "event",
        "level",
        "duration_ms",
    }
)


def _make_record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="src.foundation.trust.application",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="membership granted",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_required_fields_matches_108_section_2_exactly():
    assert set(REQUIRED_FIELDS) == _SPEC_108_S2_FIELDS


def test_required_fields_has_no_duplicates():
    assert len(REQUIRED_FIELDS) == len(set(REQUIRED_FIELDS))


def test_structured_log_line_declares_every_required_field():
    model_fields = set(StructuredLogLine.model_fields)
    missing = _SPEC_108_S2_FIELDS - model_fields
    assert missing == set()


def test_from_record_populates_required_fields_from_context():
    with bind_system("foundation.trust.application") as ctx:
        record = _make_record(event="membership_granted", duration_ms=12.4, payload={"n": 1})
        line = from_record(record, ctx)

    assert line.trace_id == str(ctx.trace_id)
    assert line.tenant_id is None
    assert line.actor_subject_id == "system"
    assert line.command_id is None
    assert line.component == "foundation.trust.application"
    assert line.event == "membership_granted"
    assert line.level == "info"
    assert line.duration_ms == 12  # round(12.4)
    assert line.message == "membership granted"
    assert line.extra == {"n": 1}


def test_from_record_defaults_event_and_duration_when_absent():
    with bind_system("foundation.trust.application") as ctx:
        line = from_record(_make_record(), ctx)

    assert line.event == "log.unstructured"
    assert line.duration_ms is None
    assert line.extra == {}


@pytest.mark.parametrize(
    ("levelname", "levelno", "expected"),
    [
        ("DEBUG", logging.DEBUG, "debug"),
        ("INFO", logging.INFO, "info"),
        ("WARNING", logging.WARNING, "warn"),
        ("ERROR", logging.ERROR, "error"),
        ("CRITICAL", logging.CRITICAL, "error"),  # §2: critical은 로그 레벨로 쓰지 않는다
    ],
)
def test_from_record_maps_stdlib_level_names(
    levelname: str, levelno: int, expected: str
) -> None:
    record = _make_record()
    record.levelname = levelname
    record.levelno = levelno

    with bind_system("foundation.trust.application") as ctx:
        line = from_record(record, ctx)

    assert line.level == expected


def test_from_record_carries_tenant_and_command_id_when_bound():
    with bind(tenant_id=uuid.uuid4(), command_id=uuid.uuid4()) as ctx:
        line = from_record(_make_record(event="order_created"), ctx)

    assert line.tenant_id == str(ctx.tenant_id)
    assert line.command_id == str(ctx.command_id)


def test_structured_log_line_rejects_unknown_level():
    with pytest.raises(ValidationError):
        StructuredLogLine(
            timestamp="2026-09-03T00:00:00Z",
            level="critical",  # type: ignore[arg-type]
            trace_id="t-1",
            actor_subject_id="system",
            component="foundation.trust.application",
            event="membership_granted",
            message="x",
        )
