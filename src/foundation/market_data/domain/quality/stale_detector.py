"""LA-5 — market_data 캔들/틱 정체(stale) 탐지.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §9.2 LA-5.

세션이 닫혀 있으면 새 캔들이 애초에 생기지 않으므로 정체가 아니다 —
`session_open`은 호출자가 `calendar.VenueCalendar.is_open`으로 판단해
넘긴다(이 모듈은 세션 판정을 재구현하지 않는다).
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
    """세션이 열려 있고 `now - last_ts`가 `k × duration(tf)`를 초과하면
    STALE(WARN). 세션이 닫혀 있거나 경과가 임계 이하이면 `None`."""
    if last_ts.tzinfo is None or now.tzinfo is None:
        raise ValueError("detect_stale은 tz-aware datetime만 받는다")
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
