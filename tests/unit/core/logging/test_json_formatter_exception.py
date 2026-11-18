"""JSONLinesFormatter exception-info output tests.

Spec: task-25777 — JSONLinesFormatter.format() must emit exception info
(type/message/stack) when record.exc_info is present, and must omit the
field entirely when it is not.  Existing fields/order/names must remain
unchanged.  Message and stack strings pass through the existing redaction
patterns (redaction.py DENY_KEYS / value-based patterns).
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from unittest.mock import patch

from src.core.logging.redaction import REDACTED
from src.core.logging.schema import JSONLinesFormatter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    *,
    msg: str = "liquidation_worker_loop: 이번 주기 실패",
    exc_info=None,
    **extra,
) -> logging.LogRecord:
    """Build a minimal LogRecord for formatter testing."""
    record = logging.LogRecord(
        name="src.services.liquidation.worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=42,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _format(record: logging.LogRecord) -> dict:
    """Format and return parsed JSON dict."""
    return json.loads(JSONLinesFormatter().format(record))


# ---------------------------------------------------------------------------
# Positive: exc_info present → exception field emitted
# ---------------------------------------------------------------------------


def test_exception_field_present_when_exc_info_given():
    """exc_info가 있으면 로그 항목에 exception 객체가 있어야 한다."""
    try:
        raise ValueError("order_id=12345")
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert "exception" in data
    exc_obj = data["exception"]
    assert exc_obj["type"] == "ValueError"
    assert "order_id=12345" in exc_obj["message"]


def test_exception_type_is_fully_qualified_class_name():
    """exception.type는 예외 클래스의 정규 이름(모듈.클래스)이어야 한다."""
    try:
        raise RuntimeError("fail")
    except RuntimeError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    # module + qualified name, e.g. "builtins.RuntimeError" or "RuntimeError"
    assert "RuntimeError" in data["exception"]["type"]


def test_exception_message_truncated_to_500_chars():
    """exception.message는 str(예외) 앞 500자까지만 포함해야 한다."""
    long_msg = "x" * 1000
    try:
        raise ValueError(long_msg)
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert len(data["exception"]["message"]) <= 500
    assert data["exception"]["message"] == "x" * 500


def test_exception_stack_truncated_to_8000_chars():
    """exception.stack은 포맷된 스택 트레이스 최대 8000자, 초과 시 [...truncated]."""
    # RecursionError tracebacks are limited by the interpreter and won't
    # exceed a few hundred chars — build a synthetic long traceback instead.
    long_tb_lines: list[str] = []
    for i in range(2000):
        long_tb_lines.append(f'  File "/fake/path_{i}.py", line {i}, in fake_func\n')
        long_tb_lines.append(f"    raise RuntimeError('frame {i}')\n")
    long_tb = (
        "Traceback (most recent call last):\n" + "".join(long_tb_lines) + "RuntimeError: boom\n"
    )

    try:
        raise RuntimeError("overflow")
    except RuntimeError:
        exc_type = sys.exc_info()
        with patch("traceback.format_exception", return_value=[long_tb]):
            record = _make_record(exc_info=exc_type)
            data = _format(record)

    stack = data["exception"]["stack"]
    assert len(stack) <= 8000
    assert stack.endswith("[truncated]")


def test_exception_stack_contains_traceback_lines():
    """exception.stack에는 포맷된 스택 트레이스 내용이 포함되어야 한다."""
    try:
        raise KeyError("missing_key")
    except KeyError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    stack = data["exception"]["stack"]
    assert "KeyError" in stack
    assert "missing_key" in stack


# ---------------------------------------------------------------------------
# Negative: exc_info None → exception field NOT present
# ---------------------------------------------------------------------------


def test_exception_field_absent_when_no_exc_info():
    """exc_info가 없으면 exception 필드 자체를 넣지 않는다 (기존 로그 모양 불변)."""
    record = _make_record(exc_info=None)
    data = _format(record)
    assert "exception" not in data


def test_exception_field_absent_when_exc_info_is_tuple_of_Nones():
    """exc_info=(None, None, None)이어도 exception 필드가 없어야 한다."""
    record = _make_record(exc_info=(None, None, None))
    data = _format(record)
    assert "exception" not in data


def test_existing_fields_unchanged_without_exc_info():
    """exc_info 없는 경우 기존 필드가 그대로 유지되어야 한다."""
    record = _make_record(
        event_type="liquidation.retry",
        correlation_id="liq-001",
        payload={"retry_count": 3},
    )
    data = _format(record)
    assert data["level"] == "ERROR"
    assert data["event_type"] == "liquidation.retry"
    assert data["correlation_id"] == "liq-001"
    assert data["extra"] == {"retry_count": 3}
    assert data["message"] == "liquidation_worker_loop: 이번 주기 실패"


# ---------------------------------------------------------------------------
# Redaction: message and stack pass through existing patterns
# ---------------------------------------------------------------------------


def test_exception_message_redacts_bearer_token():
    """message에 Bearer 토큰 패턴이 있으면 마스킹되어야 한다."""
    try:
        raise ValueError("auth: Bearer abc.def.ghi")
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert REDACTED in data["exception"]["message"]
    assert "abc.def.ghi" not in data["exception"]["message"]


def test_exception_stack_redacts_bearer_token():
    """스택 트레이스에 Bearer 토큰이 포함되어 있으면 마스킹되어야 한다."""
    # Patch format_exception to inject a token into the stack string.
    original_format_exception = traceback.format_exception

    def _fake_format_exception(etype, value, tb, limit=None):
        lines = original_format_exception(etype, value, tb, limit)
        lines.append("    token = Bearer xyz.secret.key\n")
        return lines

    try:
        raise ValueError("stack test")
    except ValueError:
        exc_type = sys.exc_info()
        with patch("traceback.format_exception", _fake_format_exception):
            record = _make_record(exc_info=exc_type)
            data = _format(record)

    assert REDACTED in data["exception"]["stack"]
    assert "xyz.secret.key" not in data["exception"]["stack"]


def test_exception_message_redacts_hex64_secret():
    """message에 64자리 16진수 시크릿이 있으면 마스킹되어야 한다."""
    hex64_secret = "a" * 64
    try:
        raise ValueError(f"key={hex64_secret}")
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert REDACTED in data["exception"]["message"]
    assert hex64_secret not in data["exception"]["message"]


def test_exception_message_redacts_jwt_like_token():
    """message에 JWT 유사 토큰(eyJ로 시작)이 있으면 마스킹되어야 한다."""
    jwt_like = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.fake.signature"
    try:
        raise ValueError(f"token={jwt_like}")
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert REDACTED in data["exception"]["message"]
    assert jwt_like not in data["exception"]["message"]


# ---------------------------------------------------------------------------
# raise from (chained exceptions): report top-level type
# ---------------------------------------------------------------------------


def test_raise_from_reports_top_level_type():
    """중첩 예외(raise from)의 경우 최상위 exception.type만 보고해야 한다."""
    try:
        try:
            raise ValueError("inner")
        except ValueError:
            raise RuntimeError("outer") from None
    except RuntimeError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert "RuntimeError" in data["exception"]["type"]
    assert "outer" in data["exception"]["message"]


# ---------------------------------------------------------------------------
# Edge: empty / minimal exception
# ---------------------------------------------------------------------------


def test_exception_with_empty_message():
    """메시지가 빈 예외도 정상 처리되어야 한다."""
    try:
        raise ValueError("")
    except ValueError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert "exception" in data
    assert data["exception"]["message"] == ""


def test_exception_with_non_string_args():
    """예외 args에 비 문자열 타입이 포함되어도 str() 변환이 되어야 한다."""
    try:
        raise TypeError(12345)
    except TypeError:
        exc_type = sys.exc_info()
        record = _make_record(exc_info=exc_type)

    data = _format(record)
    assert "exception" in data
    assert "12345" in data["exception"]["message"]
