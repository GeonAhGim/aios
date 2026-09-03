"""DC-7 — 커버리지 갭 fail-closed 판정(순수), LA-5 재사용.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-7(선행 DC-6), §9.2 DC-7.

갭 탐지 알고리즘을 새로 짜지 않는다 — 캔들 결측 탐지는
`domain/quality/gap_detector.detect_gaps`(LA-5)에, 세션·휴장 판정은
`VenueCalendar`(LA-3)에, 타임프레임 정렬은 `domain/timeframe`(LA-2)에
위임한다(LA-19 위임 원칙). DC-6 `registry.merge_spans`도 재사용한다 —
이 파일은 그 위에 "커버리지 선언 대비 실제 캔들" 대조 판정만 얹는다.

세 갈래 판정: (1) 세션 밖(휴장·마감 후)은 애초에 기대 집합에 없으므로
갭이 아니다(LA-5와 동일 의미론). (2) 세션 안인데 커버리지 선언이 없으면
`GapReason.NOT_COVERED`. (3) 세션 안이고 선언도 있는데 실제 캔들이
없으면 `GapReason.MISSING_CANDLES`(LA-5 결과 그대로).

판정 전제(요청 timeframe과 다른 축 span 혼입, 서로 다른 instrument span
혼입, naive 시각, 역전된 구간)가 깨지면 "갭 없음"(빈 리스트)을 반환하지
않는다 — 커버리지가 충분하다는 오판으로 이어질 수 있으므로
`IndeterminateCoverageError`로 fail-closed 표면화한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from src.foundation.market_data.contracts.v1 import CandleRecord, SessionWindow, Timeframe
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.coverage.registry import merge_spans
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import duration, expected_opens

__all__ = ["GapReason", "CoverageGap", "IndeterminateCoverageError", "plan_fetch"]


class IndeterminateCoverageError(ValueError):
    """판정 불가(fail-closed) — 빈 갭 목록은 "충분히 커버됨"으로 오독될 위험이 있다."""


class GapReason(str, Enum):
    NOT_COVERED = "NOT_COVERED"
    MISSING_CANDLES = "MISSING_CANDLES"


@dataclass(frozen=True, order=True)
class CoverageGap:
    """`[start_at, end_at)` 반개구간 — `CoverageSpan`과 동일 의미론."""

    start_at: datetime
    end_at: datetime
    reason: GapReason


def _validate_axis(spans: Sequence[CoverageSpan], tf: Timeframe) -> None:
    mismatched_tf = [s for s in spans if s.timeframe != tf]
    if mismatched_tf:
        raise IndeterminateCoverageError(
            f"{len(mismatched_tf)}개 span의 timeframe이 요청한 {tf.value}와 다르다 — "
            "축이 섞인 입력은 커버리지 판정을 신뢰할 수 없다(fail-closed)."
        )
    instrument_ids = {s.instrument_id for s in spans}
    if len(instrument_ids) > 1:
        raise IndeterminateCoverageError(
            f"spans에 서로 다른 instrument_id가 섞여 있다: {sorted(instrument_ids)} — "
            "단일 instrument 축만 판정 가능하다(fail-closed)."
        )


def _sessions_in_range(
    calendar: VenueCalendar, range_start: datetime, range_end: datetime
) -> list[SessionWindow]:
    windows: list[SessionWindow] = []
    day = calendar.trading_day_of(range_start)
    end_day = calendar.trading_day_of(range_end)
    while day <= end_day:
        for window in calendar.sessions_for(day):
            clip_start = max(window.open_at, range_start)
            clip_end = min(window.close_at, range_end)
            if clip_start < clip_end:
                windows.append(
                    SessionWindow(open_at=clip_start, close_at=clip_end, kind=window.kind)
                )
        day += timedelta(days=1)
    return windows


def _intersect_windows(
    sessions: Sequence[SessionWindow], covered_spans: Sequence[CoverageSpan]
) -> list[SessionWindow]:
    result: list[SessionWindow] = []
    for session in sessions:
        for span in covered_spans:
            start = max(session.open_at, span.start_at)
            end = min(session.close_at, span.end_at)
            if start < end:
                result.append(SessionWindow(open_at=start, close_at=end, kind=session.kind))
    return result


def _coalesce(points: Sequence[datetime], step: timedelta, reason: GapReason) -> list[CoverageGap]:
    """연속한(간격이 정확히 `step`인) open_time들을 하나의 `CoverageGap`으로 묶는다."""
    if not points:
        return []
    ordered = sorted(points)
    gaps: list[CoverageGap] = []
    seg_start = ordered[0]
    prev = ordered[0]
    for point in ordered[1:]:
        if point == prev + step:
            prev = point
            continue
        gaps.append(CoverageGap(seg_start, prev + step, reason))
        seg_start = point
        prev = point
    gaps.append(CoverageGap(seg_start, prev + step, reason))
    return gaps


def plan_fetch(
    *,
    spans: Sequence[CoverageSpan],
    candles: Sequence[CandleRecord],
    tf: Timeframe,
    calendar: VenueCalendar,
    range_start: datetime,
    range_end: datetime,
) -> list[CoverageGap]:
    """`spans`(DC-6 `coverage_for` 결과 등, 단일 instrument×tf 축)와 `candles`
    실측을 `[range_start, range_end)`에서 대조해, 항상 `start_at` 기준
    결정론적으로 정렬된 갭 목록을 반환한다(입력 순서 무관)."""
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise IndeterminateCoverageError(
            "range_start/range_end는 tz-aware datetime이어야 한다(fail-closed)."
        )
    if range_end < range_start:
        raise IndeterminateCoverageError(
            f"range_start > range_end: {range_start!r} > {range_end!r}(fail-closed 구간 역전)."
        )
    _validate_axis(spans, tf)
    sessions = _sessions_in_range(calendar, range_start, range_end)
    if not sessions:
        return []
    merged = merge_spans(spans)
    covered_windows = _intersect_windows(sessions, merged)
    step = duration(tf)
    all_expected = set(expected_opens(range_start, range_end, tf, sessions))
    covered_expected = set(expected_opens(range_start, range_end, tf, covered_windows))
    not_covered = sorted(all_expected - covered_expected)
    missing = sorted(
        issue.open_time
        for issue in detect_gaps(list(candles), tf, covered_windows)
        if issue.open_time is not None
    )
    gaps = _coalesce(not_covered, step, GapReason.NOT_COVERED)
    gaps += _coalesce(missing, step, GapReason.MISSING_CANDLES)
    return sorted(gaps)
