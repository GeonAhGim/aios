"""DC-10 — domain/aggregation/timeframe_rollup 단위 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-10, §4.1, §9.2 DC-10.

핵심 케이스: M1→5m/1h 롤업이 결정론적(같은 입력=바이트 동일 출력)이고
세션 경계·조기폐장에서 경계 캔들이 정확하며, 커버리지 밖 구간(내부 갭)을
0/NaN으로 채우지 않고(§4.1 fail-closed) rollup_version 없는 산출을 만들지
않는다.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.aggregation.timeframe_rollup import (
    ROLLUP_VERSION,
    InvalidRollupTargetError,
    UnsortedCandlesError,
    rollup,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")


def _bitget_calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _krx_calendar(early_closes: dict[date, time] | None = None) -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.KIS_KRX.value]
    return VenueCalendar(
        venue=Venue.KIS_KRX.value, tz=spec.tz, regular=spec, early_closes=early_closes or {}
    )


def _m1_columns(rows: list[tuple[datetime, int, int, int, int, int, int | None]]) -> CandleColumns:
    return CandleColumns(
        ts=[r[0] for r in rows],
        open=[Decimal(r[1]) for r in rows],
        high=[Decimal(r[2]) for r in rows],
        low=[Decimal(r[3]) for r in rows],
        close=[Decimal(r[4]) for r in rows],
        volume=[Decimal(r[5]) for r in rows],
        quote_volume=[None if r[6] is None else Decimal(r[6]) for r in rows],
    )


def _minute(i: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i)


def _minutes_rows(n: int) -> list[tuple[datetime, int, int, int, int, int, int | None]]:
    return [
        (_minute(i), 100 + i, 110 + i, 90 + i, 105 + i, 10, 1)
        for i in range(n)
    ]


# ---- 기본 집계 정확성 ----


def test_rollup_m1_to_m5_aggregates_ohlcv_correctly() -> None:
    columns = _m1_columns(_minutes_rows(10))
    result = rollup(columns, Timeframe.M5, _bitget_calendar())

    assert result.columns.ts == [_minute(0), _minute(5)]
    assert result.columns.open == [Decimal(100), Decimal(105)]
    assert result.columns.high == [Decimal(114), Decimal(119)]
    assert result.columns.low == [Decimal(90), Decimal(95)]
    assert result.columns.close == [Decimal(109), Decimal(114)]
    assert result.columns.volume == [Decimal(50), Decimal(50)]
    assert result.columns.quote_volume == [Decimal(5), Decimal(5)]


def test_rollup_target_m1_rejected() -> None:
    """§4.1: 파생 TF는 M1에서만 생성 — M1 자신으로의 롤업은 정의되지 않는다."""
    columns = _m1_columns(_minutes_rows(3))
    with pytest.raises(InvalidRollupTargetError):
        rollup(columns, Timeframe.M1, _bitget_calendar())


# ---- 결정론(같은 입력 = 바이트 동일 출력) ----


def test_rollup_is_deterministic_for_same_input() -> None:
    columns = _m1_columns(_minutes_rows(15))
    calendar = _bitget_calendar()
    first = rollup(columns, Timeframe.M5, calendar)
    second = rollup(columns, Timeframe.M5, calendar)
    assert first == second


# ---- rollup_version: 절대 빠뜨리지 않는다 ----


def test_rollup_version_present_and_stable() -> None:
    columns = _m1_columns(_minutes_rows(5))
    result = rollup(columns, Timeframe.M5, _bitget_calendar())
    assert result.rollup_version == ROLLUP_VERSION
    assert result.rollup_version  # 빈 문자열이 아니다


def test_rollup_empty_input_still_carries_rollup_version() -> None:
    """negative test: 입력이 비어도 `RollupResult`는 rollup_version 없는
    산출을 만들지 않는다 — 빈 columns와 짝을 이뤄 항상 버전을 반환한다."""
    empty = _m1_columns([])
    result = rollup(empty, Timeframe.M5, _bitget_calendar())
    assert len(result.columns) == 0
    assert result.rollup_version == ROLLUP_VERSION


# ---- fail-closed: 갭을 0/NaN으로 채우지 않는다 ----


def test_rollup_internal_gap_is_skipped_not_zero_filled() -> None:
    """negative test: 09:05~09:09 구간에 소스 M1이 전혀 없으면(내부 갭) 그
    구간의 파생 5분봉을 아예 만들지 않는다 — 0으로 채운 가짜 캔들을 절대
    반환하지 않는다(§4.1)."""
    rows = _minutes_rows(5) + [
        (_minute(i), 200 + i, 210 + i, 190 + i, 205 + i, 10, 1) for i in range(10, 15)
    ]
    columns = _m1_columns(rows)
    result = rollup(columns, Timeframe.M5, _bitget_calendar())

    assert result.columns.ts == [_minute(0), _minute(10)]
    assert Decimal(0) not in result.columns.volume
    assert all(v == Decimal(50) for v in result.columns.volume)


def test_rollup_quote_volume_none_when_any_source_row_missing_it() -> None:
    """negative test: 구간 안 어느 M1 행이라도 quote_volume이 없으면(None)
    그 값을 0으로 합산해 채우지 않고 None으로 둔다(모르는 값을 0으로
    가장하지 않는다)."""
    rows = [
        (_minute(0), 100, 110, 90, 105, 10, 1),
        (_minute(1), 101, 111, 91, 106, 10, None),
        (_minute(5), 105, 115, 95, 109, 10, 2),
        (_minute(6), 106, 116, 96, 110, 10, 3),
    ]
    columns = _m1_columns(rows)
    result = rollup(columns, Timeframe.M5, _bitget_calendar())

    assert result.columns.ts == [_minute(0), _minute(5)]
    assert result.columns.quote_volume == [None, Decimal(5)]


# ---- 세션 경계·조기폐장에서 경계 캔들 정확성 ----


def test_rollup_clips_boundary_candle_to_early_close() -> None:
    """조기폐장(09:07 KST)이 M5 그리드 경계(09:05~09:10)를 자르는 경우,
    09:07 이후에 (잘못) 존재하는 M1 데이터는 집계에 들어가면 안 된다 —
    경계 캔들은 세션 close_at까지만 집계한다."""
    day = date(2026, 9, 4)  # 금요일, 정규 거래일(test_session_rules.py와 동일 전제)
    calendar = _krx_calendar(early_closes={day: time(9, 7)})

    def _kst_minute(hour: int, minute: int) -> datetime:
        return datetime(2026, 9, 4, hour, minute, tzinfo=KST)

    rows = [
        (_kst_minute(9, 5), 100, 105, 95, 101, 10, None),
        (_kst_minute(9, 6), 102, 106, 96, 103, 10, None),
        # 09:07 이후는 세션이 이미 닫혔다 — 아래 값이 결과에 반영되면 버그.
        (_kst_minute(9, 7), 999, 999, 1, 999, 999, None),
        (_kst_minute(9, 8), 999, 999, 1, 999, 999, None),
    ]
    columns = _m1_columns(rows)
    result = rollup(columns, Timeframe.M5, calendar)

    assert result.columns.ts == [_kst_minute(9, 5)]
    assert result.columns.open == [Decimal(100)]
    assert result.columns.close == [Decimal(103)]
    assert result.columns.high == [Decimal(106)]
    assert result.columns.low == [Decimal(95)]
    assert result.columns.volume == [Decimal(20)]


# ---- 입력 방어(fail-closed) ----


def test_rollup_rejects_mismatched_column_lengths() -> None:
    columns = _m1_columns(_minutes_rows(3))
    broken = CandleColumns(
        ts=columns.ts,
        open=columns.open,
        high=columns.high,
        low=columns.low,
        close=columns.close[:-1],
        volume=columns.volume,
        quote_volume=columns.quote_volume,
    )
    with pytest.raises(MismatchedColumnLengthError):
        rollup(broken, Timeframe.M5, _bitget_calendar())


def test_rollup_rejects_unsorted_input() -> None:
    """negative test: 정렬되지 않은 입력은 두-포인터 집계를 조용히 어긋난
    구간과 짝짓는다 — fail-closed로 거부한다."""
    rows = _minutes_rows(3)
    rows[0], rows[1] = rows[1], rows[0]
    columns = _m1_columns(rows)
    with pytest.raises(UnsortedCandlesError):
        rollup(columns, Timeframe.M5, _bitget_calendar())
