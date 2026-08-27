import json
import logging

from src.core.logging.schema import JSONLinesFormatter, LogEntry, configure_logging


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


def test_configure_logging_attaches_json_formatter_to_root():
    configure_logging(level="DEBUG")
    root = logging.getLogger()

    assert len(root.handlers) == 1
    assert isinstance(root.handlers[0].formatter, JSONLinesFormatter)
    assert root.level == logging.DEBUG
