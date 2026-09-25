"""LA-5 — Detect stale market_data candles/ticks.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §9.2 LA-5.

When the session is closed, no new candles can form, so that is not staleness —
`session_open` is decided by the caller via `calendar.VenueCalendar.is_open`
(this module does not reimplement session adjudication).
"""
from __future__ import annotations

from datetime import datetime

from src.foundation.market_data.contracts.v1 import (
    QualityIssue,
    QualityIssueType,
    Severity,
    Timeframe,
)
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["detect_stale"]


def detect_stale(
    last_ts: datetime, now: datetime, tf: Timeframe, session_open: bool, k: int = 3
) -> QualityIssue | None:
    """Return STALE(WARN) when the session is open and `now - last_ts` exceeds
    `k × duration(tf)`. Returns `None` when the session is closed or elapsed
    time is within the threshold."""
    if last_ts.tzinfo is None or now.tzinfo is None:
        raise ValueError("detect_stale accepts tz-aware datetime only")
    if not session_open:
        return None
    threshold = k * duration(tf)
    elapsed = now - last_ts
    if elapsed <= threshold:
        return None
    return QualityIssue(
        type=QualityIssueType.STALE,
        severity=Severity.WARN,
        open_time=last_ts,
        detail={
            "elapsed_s": str(elapsed.total_seconds()),
            "threshold_s": str(threshold.total_seconds()),
        },
    )
