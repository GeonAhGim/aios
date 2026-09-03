"""DC-10 — M1 → 파생 타임프레임 결정론 집계.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-10, §4.1, §9.2 DC-10.

M1 캔들 컬럼(ADR-A `CandleColumns`, 재정의 금지)만 입력으로 받아 상위
타임프레임으로 집계한다. 타임프레임 그리드 정렬(`align_open`/
`expected_opens`)과 세션 판정(`VenueCalendar`)은 LA-2/LA-3에 위임하고
재구현하지 않는다(LA-19 위임 원칙). 순수 함수만 — I/O·asyncpg 금지.

fail-closed 규칙(§4.1):
- 파생 TF는 M1에서만 생성 — 대상 timeframe이 M1이면 거부한다.
- 커버리지 밖 구간을 0/NaN으로 채우지 않는다 — 세션 안에서 기대되는
  캔들이지만 그 구간에 소스 M1 행이 하나도 없으면(갭) 그 파생 캔들을
  아예 만들지 않는다(0으로 채운 가짜 캔들을 만들지 않는다).
- `quote_volume`은 구간 안의 어느 M1 행이라도 값이 없으면(`None`) 합계를
  `None`으로 둔다 — 모르는 값을 0으로 합산하면 §4.1을 어기는 셈이다.
- `rollup_version` 없는 산출은 만들 수 없다 — `RollupResult`가 항상
  같이 들고 다니므로 타입 수준에서 강제된다.

`rollup_version`은 BT-9 재현 키(`reproducibility_key`) 구성요소다. 사람이
수동으로 올려야 하는 상수 문자열이면 집계 규칙을 바꾸고 버전을 올리는
걸 잊기 쉽다 — 그래서 이 모듈은 집계 규칙을 기술한 `_ROLLUP_RULE_SPEC`
문자열의 해시로 버전을 정의한다: 규칙 서술이 바뀌면(즉 이 모듈의 집계
로직을 바꾸면서 그 서술도 함께 고치면) 값이 자동으로 바뀐다. 코드
로직만 바뀌고 `_ROLLUP_RULE_SPEC`은 그대로 두면 버전이 바뀌지 않는
한계는 남는다(정직하게 남겨 두는 편차) — 규칙을 바꿀 때 서술도 함께
고치는 리뷰 규율에 의존한다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)
from src.foundation.market_data.domain.timeframe import duration, expected_opens

__all__ = [
    "RollupResult",
    "InvalidRollupTargetError",
    "UnsortedCandlesError",
    "SessionNotFoundError",
    "ROLLUP_VERSION",
    "rollup",
]

_ROLLUP_RULE_SPEC = (
    "open=first_m1_open;high=max_m1_high;low=min_m1_low;close=last_m1_close;"
    "volume=sum_m1_volume;quote_volume=sum_or_none_if_any_missing;"
    "grid=epoch_aligned_utc(D1=utc_midnight_via_align_open);"
    "session_boundary=clip_window_end_to_session_close_at;"
    "gap_policy=skip_window_with_zero_source_m1_rows;"
    "source_timeframe=M1_only"
)
ROLLUP_VERSION = f"tfr1-{hashlib.sha256(_ROLLUP_RULE_SPEC.encode('utf-8')).hexdigest()[:16]}"


class InvalidRollupTargetError(ValueError):
    """`MD_ROLLUP_TARGET_INVALID` — M1은 롤업 대상이 될 수 없다(§4.1: 파생
    TF는 M1에서만 생성되므로, M1 자신으로의 "롤업"은 정의되지 않는다)."""

    def __init__(self, tf: Timeframe) -> None:
        super().__init__(f"M1은 롤업 대상 timeframe이 될 수 없습니다: {tf!r}")


class UnsortedCandlesError(ValueError):
    """`MD_ROLLUP_UNSORTED_INPUT` — 정렬되지 않은 M1 입력은 두-포인터 집계를
    조용히 어긋난 구간과 짝짓는다(fail-closed로 거부)."""


class SessionNotFoundError(ValueError):
    """`MD_ROLLUP_SESSION_NOT_FOUND` — `expected_opens`가 반환한 open이
    그 open을 낳은 세션 목록에서 다시 찾아지지 않는다(내부 불변 위반,
    발생하면 안 되지만 방어적으로 거부한다)."""


@dataclass(frozen=True, slots=True)
class RollupResult:
    """`rollup` 한 번의 산출. `rollup_version` 없이 `columns`만 꺼내 쓸 수
    없도록 항상 짝을 이뤄 반환한다(§4.1: rollup_version 없는 파생 캔들
    저장 금지 — 저장 전에 이미 타입으로 강제)."""

    columns: CandleColumns
    rollup_version: str


def _sessions_between(
    calendar: VenueCalendar, start_ts: datetime, end_ts: datetime
) -> list[SessionWindow]:
    day = calendar.trading_day_of(start_ts)
    end_day = calendar.trading_day_of(end_ts)
    sessions: list[SessionWindow] = []
    while day <= end_day:
        sessions.extend(calendar.sessions_for(day))
        day += timedelta(days=1)
    return sessions


def _session_containing(ts_open: datetime, sessions: list[SessionWindow]) -> SessionWindow:
    for session in sessions:
        if session.open_at <= ts_open < session.close_at:
            return session
    raise SessionNotFoundError(f"open_time={ts_open!r}에 대응하는 세션을 찾을 수 없습니다")


def rollup(columns: CandleColumns, tf: Timeframe, calendar: VenueCalendar) -> RollupResult:
    """`columns`(M1 소스, `open_time` 오름차순)를 `tf` 캔들로 집계한다.

    같은 입력(`columns`·`tf`·`calendar`)이면 항상 바이트 동일한 출력을
    낸다 — 정렬 순서·세션 창을 결정하는 값은 전부 인자로만 받고 전역
    시계·난수를 쓰지 않는다.
    """
    if tf is Timeframe.M1:
        raise InvalidRollupTargetError(tf)

    n = len(columns)
    lengths = {
        "open": len(columns.open),
        "high": len(columns.high),
        "low": len(columns.low),
        "close": len(columns.close),
        "volume": len(columns.volume),
        "quote_volume": len(columns.quote_volume),
    }
    if any(length != n for length in lengths.values()):
        raise MismatchedColumnLengthError({"ts": n, **lengths})
    if n == 0:
        return RollupResult(columns=_empty_columns(), rollup_version=ROLLUP_VERSION)
    for i in range(n - 1):
        if columns.ts[i] > columns.ts[i + 1]:
            raise UnsortedCandlesError(
                f"index {i}의 open_time({columns.ts[i]!r})이 index {i + 1}"
                f"({columns.ts[i + 1]!r})보다 늦습니다"
            )

    step = duration(tf)
    m1_step = duration(Timeframe.M1)
    range_start = columns.ts[0]
    range_end = columns.ts[-1] + m1_step
    sessions = _sessions_between(calendar, range_start, range_end)
    opens = expected_opens(range_start, range_end, tf, sessions)

    out_ts: list[datetime] = []
    out_open: list[Decimal] = []
    out_high: list[Decimal] = []
    out_low: list[Decimal] = []
    out_close: list[Decimal] = []
    out_volume: list[Decimal] = []
    out_quote_volume: list[Decimal | None] = []

    idx = 0
    for ts_open in opens:
        session = _session_containing(ts_open, sessions)
        window_end = min(ts_open + step, session.close_at)
        while idx < n and columns.ts[idx] < ts_open:
            idx += 1
        j = idx
        first_open: Decimal | None = None
        last_close = Decimal("0")
        highs: list[Decimal] = []
        lows: list[Decimal] = []
        volume_sum = Decimal("0")
        quote_volumes: list[Decimal] = []
        quote_volume_known = True
        while j < n and columns.ts[j] < window_end:
            if first_open is None:
                first_open = columns.open[j]
            last_close = columns.close[j]
            highs.append(columns.high[j])
            lows.append(columns.low[j])
            volume_sum += columns.volume[j]
            qv = columns.quote_volume[j]
            if qv is None:
                quote_volume_known = False
            else:
                quote_volumes.append(qv)
            j += 1

        if first_open is None:
            idx = j
            continue  # 갭: 0/NaN으로 채우지 않고 이 파생 캔들을 만들지 않는다

        out_ts.append(ts_open)
        out_open.append(first_open)
        out_high.append(max(highs))
        out_low.append(min(lows))
        out_close.append(last_close)
        out_volume.append(volume_sum)
        out_quote_volume.append(
            sum(quote_volumes, start=Decimal("0")) if quote_volume_known else None
        )
        idx = j

    result_columns = CandleColumns(
        ts=out_ts,
        open=out_open,
        high=out_high,
        low=out_low,
        close=out_close,
        volume=out_volume,
        quote_volume=out_quote_volume,
    )
    return RollupResult(columns=result_columns, rollup_version=ROLLUP_VERSION)


def _empty_columns() -> CandleColumns:
    return CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])
