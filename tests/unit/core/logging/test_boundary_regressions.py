"""PLT-02/03 logging boundary regressions (task-9986).

Run explicitly with pytest tests/unit/core/logging/__init__.py.
"""

import io
import json
import logging

import pytest
from pydantic import ValidationError

from src.core.logging import redaction
from src.core.logging.schema import JSONLinesFormatter


def _record(**attributes):
    record = logging.LogRecord("logging.boundary", logging.INFO, __file__, 1, "event", (), None)
    for key, value in attributes.items():
        setattr(record, key, value)
    return record


@pytest.mark.parametrize(
    ("attributes", "field", "error_type"),
    [
        ({"payload": ["invalid"]}, "extra", "dict_type"),
        ({"event_type": None}, "event_type", "string_type"),
        ({"correlation_id": {"invalid": True}}, "correlation_id", "string_type"),
    ],
    ids=["negative-payload", "negative-event", "negative-correlation"],
)
def test_negative_formatter_rejects_invalid_contract(attributes, field, error_type):
    with pytest.raises(ValidationError) as caught:
        JSONLinesFormatter().format(_record(**attributes))
    assert any(
        error["loc"] == (field,) and error["type"] == error_type for error in caught.value.errors()
    )


def _handler(stream):
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JSONLinesFormatter())
    handler.addFilter(redaction.RedactionFilter())
    return handler


def test_failure_injection_redaction_error_prevents_sink_write(monkeypatch):
    stream = io.StringIO()
    handler = _handler(stream)
    record = _record(payload={"password": "synthetic-test-value"})
    failure = RuntimeError("injected redactor failure")

    def broken_redact(payload):
        raise failure

    monkeypatch.setattr(redaction, "redact", broken_redact)
    try:
        with pytest.raises(RuntimeError) as caught:
            handler.handle(record)
        assert caught.value is failure
        assert stream.getvalue() == ""
    finally:
        handler.close()


def _assert_sink_redacted(handler, stream):
    handler.handle(_record(payload={"password": "synthetic-test-value"}))
    line = json.loads(stream.getvalue())
    assert line["extra"]["password"] == redaction.REDACTED
    assert "synthetic-test-value" not in stream.getvalue()


def test_red_gate_reproduction_detects_redaction_bypass(monkeypatch):
    stream = io.StringIO()
    handler = _handler(stream)
    try:
        _assert_sink_redacted(handler, stream)
        stream.seek(0)
        stream.truncate()
        monkeypatch.setattr(redaction, "redact", dict)
        with pytest.raises(AssertionError):
            _assert_sink_redacted(handler, stream)
    finally:
        handler.close()


@pytest.mark.perf
def test_formatter_p95_within_logging_budget(perf_budget):
    # ADR-2026-09-09-C has no logging budget; retain test_schema.py's local 5 ms budget.
    formatter = JSONLinesFormatter()
    record = _record(payload={"count": 1})
    samples = perf_budget.samples(lambda: formatter.format(record), n=100)
    assert sorted(sample.cpu_ms for sample in samples)[94] < 5.0
