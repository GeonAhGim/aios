"""LA-2 — market_data/domain/timeframe.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-2, §8.1, §9.2 LA-2.

핵심 케이스(§8.1): `align_open` 경계(정각·D1 UTC 기준), `expected_opens`가
세션 밖 시각을 절대 만들지 않음, 알 수 없는 tf → 예외.
"""
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe
from src.foundation.market_data.domain.timeframe import (
    UnknownTimeframeError,
    align_open,
    duration,
    expected_opens,
)

UTC = timezone.utc


def _session(open_at: datetime, close_at: datetime) -> SessionWindow:
    return SessionWindow(open_at=open_at, close_at=close_at, kind="REGULAR")


@pytest.mark.parametrize(
    ("tf", "expected"),
    [
        (Timeframe.M1, timedelta(minutes=1)),
        (Timeframe.M5, timedelta(minutes=5)),
        (Timeframe.M15, timedelta(minutes=15)),
        (Timeframe.M30, timedelta(minutes=30)),
        (Timeframe.H1, timedelta(hours=1)),
        (Timeframe.H4, timedelta(hours=4)),
        (Timeframe.D1, timedelta(days=1)),
    ],
)
def test_duration_known_timeframes(tf: Timeframe, expected: timedelta) -> None:
    assert duration(tf) == expected


def test_duration_unknown_timeframe_raises() -> None:
    with pytest.raises(UnknownTimeframeError):
        duration(cast(Timeframe, "2h"))


@pytest.mark.parametrize(
    ("tf", "ts", "expected_open"),
    [
        (
            Timeframe.M1,
            datetime(2026, 9, 3, 10, 5, 30, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 5, tzinfo=UTC),
        ),
        (
            Timeframe.M5,
            datetime(2026, 9, 3, 10, 7, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 5, tzinfo=UTC),
        ),
        (
            Timeframe.M5,
            datetime(2026, 9, 3, 10, 5, 0, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 5, tzinfo=UTC),
        ),
        (
            Timeframe.M15,
            datetime(2026, 9, 3, 10, 44, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 30, tzinfo=UTC),
        ),
        (
            Timeframe.M30,
            datetime(2026, 9, 3, 10, 59, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 30, tzinfo=UTC),
        ),
        (
            Timeframe.H1,
            datetime(2026, 9, 3, 10, 59, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 10, 0, tzinfo=UTC),
        ),
        (
            Timeframe.H4,
            datetime(2026, 9, 3, 11, 59, 59, tzinfo=UTC),
            datetime(2026, 9, 3, 8, 0, tzinfo=UTC),
        ),
    ],
)
def test_align_open_boundary(tf: Timeframe, ts: datetime, expected_open: datetime) -> None:
    assert align_open(ts, tf) == expected_open


def test_align_open_d1_uses_utc_midnight() -> None:
    ts = datetime(2026, 9, 3, 23, 59, 59, tzinfo=UTC)
    assert align_open(ts, Timeframe.D1) == datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


def test_align_open_d1_normalizes_non_utc_tz_first() -> None:
    kst = timezone(timedelta(hours=9))
    ts = datetime(2026, 9, 4, 8, 30, tzinfo=kst)  # == 2026-09-03 23:30 UTC
    assert align_open(ts, Timeframe.D1) == datetime(2026, 9, 3, 0, 0, tzinfo=UTC)


def test_align_open_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        align_open(datetime(2026, 9, 3, 10, 0), Timeframe.M1)


def test_align_open_unknown_timeframe_raises() -> None:
    with pytest.raises(UnknownTimeframeError):
        align_open(datetime(2026, 9, 3, 10, 0, tzinfo=UTC), cast(Timeframe, "2h"))


def test_expected_opens_never_produces_out_of_session_times() -> None:
    session = _session(
        datetime(2026, 9, 3, 9, 3, tzinfo=UTC),  # tf 경계에 정렬돼 있지 않음
        datetime(2026, 9, 3, 9, 47, tzinfo=UTC),
    )
    opens = expected_opens(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
        Timeframe.M5,
        [session],
    )
    assert opens  # 최소 1개는 생성돼야 아래 불변식 검증이 의미 있다
    assert all(session.open_at <= t < session.close_at for t in opens)
    # session.open_at(09:03)보다 앞선 정렬 캔들(09:00)은 절대 만들지 않는다
    assert all(t >= datetime(2026, 9, 3, 9, 5, tzinfo=UTC) for t in opens)


def test_expected_opens_skips_gap_between_sessions() -> None:
    morning = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    )
    afternoon = _session(
        datetime(2026, 9, 3, 6, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 6, 30, tzinfo=UTC),
    )
    opens = expected_opens(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
        Timeframe.H1,
        [morning, afternoon],
    )
    assert opens == [
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 6, 0, tzinfo=UTC),
    ]


def test_expected_opens_clips_to_start_end_range() -> None:
    session = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 4, 0, tzinfo=UTC),
    )
    opens = expected_opens(
        datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 3, 0, tzinfo=UTC),
        Timeframe.H1,
        [session],
    )
    assert opens == [
        datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
    ]


def test_expected_opens_empty_sessions_returns_empty() -> None:
    opens = expected_opens(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
        Timeframe.M1,
        [],
    )
    assert opens == []


def test_expected_opens_rejects_naive_start() -> None:
    session = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 4, 0, tzinfo=UTC),
    )
    with pytest.raises(ValueError, match="tz-aware"):
        expected_opens(
            datetime(2026, 9, 3, 0, 0),
            datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
            Timeframe.H1,
            [session],
        )


def test_expected_opens_unknown_timeframe_raises() -> None:
    session = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 4, 0, tzinfo=UTC),
    )
    with pytest.raises(UnknownTimeframeError):
        expected_opens(
            datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
            datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
            cast(Timeframe, "2h"),
            [session],
        )
