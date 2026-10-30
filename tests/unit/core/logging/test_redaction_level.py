"""PLT-02 negative tests: StructuredLogLine.level accepts only the 4 Literal values.

Split out of test_redaction.py (near the 500-line ratchet) by responsibility.
"""

from datetime import UTC, datetime
from typing import Any

import pytest

from src.core.logging.fields import StructuredLogLine


def _line(level: Any) -> StructuredLogLine:  # invalid inputs are the point of these tests
    return StructuredLogLine(
        timestamp=datetime.now(UTC),
        level=level,
        message="test message",
        component="test",
        event="test_event",
        correlation_id="corr-1",
        tenant_id="tenant-1",
        trace_id="trace-1",
        span_id="span-1",
        session_id="session-1",
        user_id="user-1",
        request_id="req-1",
        endpoint="/api/test",
        method="GET",
        status_code=200,
        duration_ms=10,
        source="test",
        metadata={},
        actor_subject_id="user-abc",
    )


@pytest.mark.parametrize(
    "level",
    ["not a level", "warning", "INFO", "Error", "trace", " info", "info "],
)
def test_rejects_near_miss_level_values(level: str) -> None:
    """Unknown, misspelled, mis-cased, or whitespace-padded levels must raise ValueError."""
    with pytest.raises(ValueError):
        _line(level)


def test_rejects_none_level() -> None:
    """level=None is not a Literal member and must raise, not be stored as 'None'."""
    with pytest.raises(ValueError):
        _line(None)
