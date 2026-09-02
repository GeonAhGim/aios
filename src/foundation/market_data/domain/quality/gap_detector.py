"""LA-5 — market_data 캔들 갭 탐지(세션 기준 기대 open_time 대비 결측).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §9.2 LA-5.

세션 밖 결측(휴장일·조기폐장 이후·정규장 마감 후)은 갭이 아니다 — 기대
집합 자체를 `sessions`(호출자가 `calendar.VenueCalendar.sessions_for`로 구한
창) 범위 안에서만 `timeframe.expected_opens`로 계산하므로, 세션 밖 시각은
애초에 기대 집합에 들어가지 않는다. 이 모듈은 거래일·세션 판정을
재구현하지 않는다 — 그 판단은 호출자가 `VenueCalendar`로 이미 끝낸 뒤
결과 세션만 여기 넘긴다.
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
    """`sessions` 안에서 `tf` 간격으로 기대되는 open_time 중 `candles`에 없는
    시각마다 GAP(WARN) 이슈 1개. `sessions`가 비어 있으면(계산 대상 없음)
    빈 리스트 — 알 수 없는 timeframe은 `expected_opens`가 fail-closed로
    예외를 낸다.
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
