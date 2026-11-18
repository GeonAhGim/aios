"""JSONLinesFormatter must surface exception info (type/message/stack) — UAT-F2.

Depends on the sibling leaf that adds the `exception` object to
`src/core/logging/schema.py`.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from src.core.logging.schema import JSONLinesFormatter

_STACK_LIMIT = 8000
_MESSAGE_LIMIT = 500
_TRUNC_SUFFIX = "...[truncated]"


class SeedKeyMissingError(RuntimeError):
    pass


def _format(exc: BaseException | None, msg: str = "worker failed") -> dict[str, Any]:
    exc_info: Any = None
    if exc is not None:
        try:
            raise exc
        except BaseException:  # noqa: BLE001 - capture any raised exception for exc_info
            exc_info = sys.exc_info()
    record = logging.LogRecord(
        name="test.worker",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=(),
        exc_info=exc_info,
    )
    return json.loads(JSONLinesFormatter().format(record))


def test_exception_log_has_type_message_stack() -> None:
    line = _format(SeedKeyMissingError("seed key missing"))
    exc = line["exception"]
    assert exc["type"].endswith("SeedKeyMissingError")
    assert "seed key missing" in exc["message"]
    assert "Traceback" in exc["stack"]
    assert "SeedKeyMissingError" in exc["stack"]


def test_no_exception_means_no_exception_field() -> None:
    line = _format(None)
    assert "exception" not in line
    assert line["message"] == "worker failed"


def test_long_message_is_cut_to_500_chars() -> None:
    line = _format(ValueError("x" * 2000))
    assert len(line["exception"]["message"]) == _MESSAGE_LIMIT


def test_oversized_stack_is_truncated() -> None:
    line = _format(ValueError("z" * 20000))
    stack = line["exception"]["stack"]
    assert stack.endswith(_TRUNC_SUFFIX)
    assert len(stack) <= _STACK_LIMIT + len(_TRUNC_SUFFIX)


def test_secret_in_exception_message_and_stack_is_masked() -> None:
    line = _format(RuntimeError("upstream said Bearer abc.def.ghi rejected"))
    exc = line["exception"]
    assert "abc.def.ghi" not in exc["message"]
    assert "abc.def.ghi" not in exc["stack"]


def test_chained_exception_reports_top_level_type() -> None:
    try:
        try:
            raise KeyError("inner")
        except KeyError as inner:
            raise SeedKeyMissingError("outer") from inner
    except SeedKeyMissingError as err:
        line = _format(err)
    exc = line["exception"]
    assert exc["type"].endswith("SeedKeyMissingError")
    assert "KeyError" in exc["stack"]
