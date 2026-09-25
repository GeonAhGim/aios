"""LA-5 — market_data candle gap detection (missing vs. expected open_time per session).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §9.2 LA-5.

Missing data outside sessions (holidays, early closes, post-market) is not a gap —
`timeframe.expected_opens` only computes expectations within the `sessions` window
(the caller obtains it via `calendar.VenueCalendar.sessions_for`), so times outside
the session range are never part of the expected set in the first place. This module
does not reimplement business-day/session logic — that judgment is already made by
the caller via `VenueCalendar`; only the resulting sessions are passed here.
"""
from __future__ import annotations

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssue,
    QualityIssueType,
    SessionWindow,
    Severity,
    Timeframe,
)
from src.foundation.market_data.domain.timeframe import expected_opens

__all__ = ["detect_gaps"]


def detect_gaps(
    candles: list[CandleRecord], tf: Timeframe, sessions: list[SessionWindow]
) -> list[QualityIssue]:
    """Yield one GAP(WARN) issue per expected open_time in `sessions` at `tf`
    interval that is missing from `candles`. If `sessions` is empty (nothing to
    compute), return an empty list — unknown timeframes cause `expected_opens` to
    raise a fail-closed exception.
    """
    if not sessions:
        return []
    start = min(s.open_at for s in sessions)
    end = max(s.close_at for s in sessions)
    expected = expected_opens(start, end, tf, sessions)
    received = {c.open_time for c in candles}
    return [
        QualityIssue(type=QualityIssueType.GAP, severity=Severity.WARN, open_time=ot, detail={})
        for ot in expected
        if ot not in received
    ]
