"""DC-7 — fail-closed coverage-gap determination (pure), reuses LA-5.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-7 (prerequisite DC-6), §9.2 DC-7.

Gap detection is delegated to `domain/quality/gap_detector.detect_gaps` (LA-5),
session/holiday calendar to `VenueCalendar` (LA-3), and timeframe alignment
to `domain/timeframe` (LA-2) (LA-19). DC-6 `registry.merge_spans` is also
reused — this file only overlays the "coverage declaration vs actual candles"
comparison on top of that.

Three-way determination: (1) Outside session (holiday/closed after market
close) is not a gap (same semantics as LA-5). (2) Inside session but no
coverage declaration → `NOT_COVERED`. (3) Declaration exists but no actual
candles → `MISSING_CANDLES` (identical to LA-5).

If preconditions for determination (mixed request timeframe/axis, naive
datetimes, reversed interval) are violated, do NOT return "no gaps" — surface
as `IndeterminateCoverageError` with fail-closed semantics.
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
    """Indeterminate coverage (fail-closed) — an empty gap list could be
    misread as "sufficiently covered"."""


class GapReason(str, Enum):
    NOT_COVERED = "NOT_COVERED"
    MISSING_CANDLES = "MISSING_CANDLES"


@dataclass(frozen=True, order=True)
class CoverageGap:
    """Left-closed, right-open interval `[start_at, end_at)` — same semantics
    as `CoverageSpan`."""

    start_at: datetime
    end_at: datetime
    reason: GapReason


def _validate_axis(spans: Sequence[CoverageSpan], tf: Timeframe, calendar: VenueCalendar) -> None:
    """Validates that spans share a single
    (instrument_id, venue, asset_class, quality_grade, tf) axis and that the
    venue matches `calendar` (fail-closed)."""
    if any(s.timeframe != tf for s in spans):
        raise IndeterminateCoverageError(
            f"spans에 요청 timeframe({tf.value})과 다른 timeframe이 섞였다(fail-closed)."
        )
    if any(s.venue.value != calendar.venue for s in spans):
        raise IndeterminateCoverageError(
            f"spans에 calendar venue({calendar.venue})와 다른 venue가 섞였다(fail-closed)."
        )
    axes = {(s.instrument_id, s.asset_class, s.quality_grade) for s in spans}
    if len(axes) > 1:
        raise IndeterminateCoverageError(
            f"spans에 서로 다른 (instrument_id, asset_class, quality_grade) 축: "
            f"{sorted(axes)}(fail-closed)."
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
    """Merge consecutive missing time-points into continuous `CoverageGap`
    segments (fail-closed)."""
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
    """Compare `spans` (result of DC-6 `coverage_for`, single-axis) against
    actual `candles` over `[range_start, range_end)` and return a
    deterministically sorted gap list by `start_at` (input order irrelevant)."""
    if range_start.tzinfo is None or range_end.tzinfo is None:
        raise IndeterminateCoverageError(
            "range_start/range_end는 tz-aware datetime이어야 한다(fail-closed)."
        )
    if range_end < range_start:
        raise IndeterminateCoverageError(
            f"range_start > range_end: {range_start!r} > {range_end!r}(fail-closed 구간 역전)."
        )
    _validate_axis(spans, tf, calendar)
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
