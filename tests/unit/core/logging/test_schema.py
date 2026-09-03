import json
import logging
import uuid
from logging.handlers import QueueHandler

from src.core.logging.fields import REQUIRED_FIELDS
from src.core.logging.redaction import REDACTED, RedactionFilter
from src.core.logging.request_context import request_id_var
from src.core.logging.schema import JSONLinesFormatter, LogEntry, configure_logging
from src.core.observability.context import bind


def _make_record(**extra) -> logging.LogRecord:
    record = logging.LogRecord(
        name="src.exchanges.bitget.adapter",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="주문 재시도",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def test_formatter_produces_valid_json_lines_matching_log_entry():
    record = _make_record(event_type="order.status.changed", correlation_id="c-1", payload={"n": 1})
    formatted = JSONLinesFormatter().format(record)

    data = json.loads(formatted)
    entry = LogEntry(**data)
    assert entry.level == "WARNING"
    assert entry.module == "src.exchanges.bitget.adapter"
    assert entry.event_type == "order.status.changed"
    assert entry.correlation_id == "c-1"
    assert entry.extra == {"n": 1}
    assert entry.message == "주문 재시도"


def test_formatter_defaults_when_extra_fields_absent():
    record = _make_record()
    entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))

    assert entry.event_type == "log.unstructured"
    assert entry.correlation_id is None
    assert entry.extra == {}


def test_formatter_falls_back_to_request_id_contextvar_when_correlation_id_absent():
    """request_id 미들웨어(src/api/middleware/request_id.py)가 이 값을
    설정해두면, 호출자가 correlation_id를 명시하지 않은 로그도 자동으로
    요청 ID가 찍혀야 한다."""
    token = request_id_var.set("req-abc123")
    try:
        record = _make_record()
        entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))
    finally:
        request_id_var.reset(token)

    assert entry.correlation_id == "req-abc123"


def test_formatter_explicit_correlation_id_wins_over_request_id_contextvar():
    """AIOSTask.task_id처럼 호출자가 의도적으로 지정한 correlation_id는
    요청 ID로 덮어써지면 안 된다."""
    token = request_id_var.set("req-abc123")
    try:
        record = _make_record(correlation_id="task-xyz")
        entry = LogEntry(**json.loads(JSONLinesFormatter().format(record)))
    finally:
        request_id_var.reset(token)

    assert entry.correlation_id == "task-xyz"


def test_configure_logging_attaches_json_formatter_to_root():
    listener = configure_logging(level="DEBUG")
    try:
        root = logging.getLogger()

        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, JSONLinesFormatter)
        assert root.level == logging.DEBUG
    finally:
        # QueueListener는 데몬 스레드를 띄운다 — 명시적으로 stop()하지 않으면
        # 다음 테스트로 스레드가 새어 나가 flaky의 원인이 된다.
        listener.stop()


def test_configure_logging_attaches_queue_handler_for_non_blocking_emit():
    """실제 stdout 쓰기는 QueueListener 스레드가 하고, 호출 스레드는 큐에 넣기만
    한다 — root에 붙는 건 QueueHandler여야 한다(§9 PLT-03 "로그 sink 지연/stdout
    막힘" 리스크 대응)."""
    listener = configure_logging(level="INFO")
    try:
        root = logging.getLogger()
        assert isinstance(root.handlers[0], QueueHandler)
    finally:
        listener.stop()


def test_configure_logging_end_to_end_emits_redacted_8_field_json_line(capsys):
    listener = configure_logging(level="INFO")
    try:
        with bind(tenant_id=uuid.uuid4()):
            logging.getLogger("src.exchanges.bitget.adapter").warning(
                "주문 재시도",
                extra={
                    "event_type": "order.status.changed",
                    "payload": {"api_key": "abcd1234", "n": 1},
                },
            )
    finally:
        # stop()은 sentinel을 넣고 리스너 스레드를 join하므로, 이미 큐에 있던
        # 레코드가 먼저 처리된 뒤에 반환된다 — sleep 없이 결정적으로 flush된다.
        listener.stop()

    line = json.loads(capsys.readouterr().err.strip().splitlines()[-1])

    for field_name in REQUIRED_FIELDS:
        assert field_name in line
    assert line["level"] == "WARNING"  # 07 §7.1 계약 유지 — 108의 소문자 매핑 아님
    assert line["extra"]["api_key"] == REDACTED
    assert line["extra"]["n"] == 1


def test_formatter_json_line_carries_all_108_required_fields():
    with bind(tenant_id=uuid.uuid4()):
        record = _make_record(
            event_type="order.status.changed", correlation_id="c-1", payload={"n": 1}
        )
        entry = json.loads(JSONLinesFormatter().format(record))

    for field_name in REQUIRED_FIELDS:
        assert field_name in entry
    # 기존 07 §7.1 필드도 함께 실려야 한다(추가만 허용, 삭제/치환 금지).
    assert entry["module"] == "src.exchanges.bitget.adapter"
    assert entry["event_type"] == "order.status.changed"


def test_formatter_preserves_legacy_level_value_over_108_lowercase_mapping():
    """108 §2의 `level`은 소문자(warn/error 등)로 매핑되지만, 07 §7.1 소비처는
    `record.levelname` 원문(WARNING 등)을 기대한다 — 필드 이름이 겹치는
    유일한 경우이므로 기존 계약이 이긴다."""
    record = _make_record()
    entry = json.loads(JSONLinesFormatter().format(record))

    assert entry["level"] == "WARNING"


def test_formatter_extra_reflects_redaction_filter_applied_before_format():
    record = _make_record(payload={"api_key": "abcd1234", "note": "ok"})
    RedactionFilter().filter(record)

    entry = json.loads(JSONLinesFormatter().format(record))

    assert entry["extra"]["api_key"] == REDACTED
    assert entry["extra"]["note"] == "ok"
