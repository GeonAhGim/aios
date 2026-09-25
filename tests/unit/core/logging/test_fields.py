import logging
import uuid
from datetime import datetime, timedelta, timezone

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
def test_from_record_maps_stdlib_level_names(levelname: str, levelno: int, expected: str) -> None:
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


def test_structured_log_line_rejects_missing_actor_subject_id():
    """negative — actor_subject_id는 기본값이 없다. 누락된 채로 생성이 성공하면 감사 로그에서
    행위자 식별이 비어있는 라인이 만들어질 수 있으므로 생성 자체가 실패해야 한다."""
    with pytest.raises(ValidationError):
        StructuredLogLine(
            timestamp="2026-09-03T00:00:00Z",
            level="info",
            trace_id="t-1",
            component="foundation.trust.application",
            event="membership_granted",
            message="x",
        )


def test_structured_log_line_rejects_unknown_field():
    """negative — pydantic 기본값(extra="ignore")이면 오타 필드(`trace__id`)가 조용히
    드롭되고 `trace_id`가 누락 검증 실패로만 드러나 원인이 가려진다. `extra="forbid"`가
    설정돼 있어야 오타 자체가 즉시 실패로 드러난다."""
    with pytest.raises(ValidationError):
        StructuredLogLine(
            timestamp="2026-09-03T00:00:00Z",
            level="info",
            trace_id="t-1",
            actor_subject_id="system",
            component="foundation.trust.application",
            event="membership_granted",
            message="x",
            trace__id="t-1",  # 오타 필드 — REQUIRED_FIELDS에 없다
        )


def test_structured_log_line_rejects_unknown_field_even_with_all_required_present():
    """negative — 필수 필드가 전부 있어도 스키마에 없는 키가 섞이면 실패해야 한다(단순
    누락 검증과는 다른 경로임을 확인)."""
    with pytest.raises(ValidationError):
        StructuredLogLine(
            timestamp="2026-09-03T00:00:00Z",
            level="info",
            trace_id="t-1",
            tenant_id=None,
            actor_subject_id="system",
            command_id=None,
            component="foundation.trust.application",
            event="membership_granted",
            duration_ms=None,
            message="x",
            unexpected_field="should not be silently ignored",
        )


def test_structured_log_line_rejects_naive_timestamp():
    """negative — naive timestamp(tzinfo 없음)은 어느 시간대인지 알 수 없어 감사 로그의
    시각을 신뢰할 수 없게 만든다. 108 §2/CLAUDE.md §3 "모든 datetime은 tz-aware UTC"
    규칙을 위반하므로 생성 시점에 거부해야 한다."""
    with pytest.raises(ValidationError):
        StructuredLogLine(
            timestamp=datetime(2026, 9, 3, 0, 0, 0),  # naive — tzinfo=None
            level="info",
            trace_id="t-1",
            actor_subject_id="system",
            component="foundation.trust.application",
            event="membership_granted",
            message="x",
        )


def test_structured_log_line_accepts_tz_aware_non_utc_offset():
    """positive — UTC가 아닌 다른 tz-aware offset(예: +09:00)도 naive는 아니므로
    허용된다. 검증은 "naive 거부"이지 "UTC 오프셋만 허용"이 아니다."""
    line = StructuredLogLine(
        timestamp=datetime(2026, 9, 3, 9, 0, 0, tzinfo=timezone(timedelta(hours=9))),
        level="info",
        trace_id="t-1",
        actor_subject_id="system",
        component="foundation.trust.application",
        event="membership_granted",
        message="x",
    )
    assert line.timestamp.utcoffset() == timedelta(hours=9)


def test_structured_log_line_extra_payload_field_still_accepted():
    """positive counterpart — `extra`는 정의된 필드이므로 임의 payload 딕셔너리를 담는
    용도는 `extra="forbid"` 이후에도 그대로 동작해야 한다."""
    line = StructuredLogLine(
        timestamp="2026-09-03T00:00:00Z",
        level="info",
        trace_id="t-1",
        actor_subject_id="system",
        component="foundation.trust.application",
        event="membership_granted",
        message="x",
        extra={"n": 1},
    )
    assert line.extra == {"n": 1}
