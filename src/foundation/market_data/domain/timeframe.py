"""LA-2 — market_data 타임프레임 순수 규칙(길이·정렬).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-2, §9.2 LA-2.

`Timeframe`/`SessionWindow`는 LA-1 계약(contracts/v1)에서 그대로 재노출한다
(FND-03: domain은 contracts를 import하되 재정의하지 않는다). 이 모듈은
순수 함수만 제공한다 — I/O·전역 시계 호출 금지, 시각은 항상 인자로 받는다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe

__all__ = [
    "Timeframe",
    "SessionWindow",
    "UnknownTimeframeError",
    "duration",
    "align_open",
    "expected_opens",
]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_DURATIONS: dict[Timeframe, timedelta] = {
    Timeframe.M1: timedelta(minutes=1),
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.M30: timedelta(minutes=30),
    Timeframe.H1: timedelta(hours=1),
    Timeframe.H4: timedelta(hours=4),
    Timeframe.D1: timedelta(days=1),
}


class UnknownTimeframeError(ValueError):
    """`MD_TIMEFRAME_UNKNOWN` — `_DURATIONS`에 등록되지 않은 timeframe."""


def duration(tf: Timeframe) -> timedelta:
    """`tf` 캔들 1개의 길이. 미등록 값은 `UnknownTimeframeError`(fail-closed)."""
    try:
        return _DURATIONS[tf]
    except KeyError as exc:
        raise UnknownTimeframeError(f"알 수 없는 timeframe: {tf!r}") from exc


def align_open(ts: datetime, tf: Timeframe) -> datetime:
    """`ts`가 속한 `tf` 캔들의 open_time(UTC, tz-aware).

    D1은 UTC 자정 경계로 정렬한다(§8.1 "D1 UTC 기준"). 그 외 타임프레임은
    UNIX epoch(UTC 자정) 기준 등간격 정렬이라 M1~H4 경계가 모두 정시
    (정각/5분/15분/30분/1시간/4시간 단위)에 맞는다.
    """
    if ts.tzinfo is None:
        raise ValueError("align_open은 tz-aware datetime만 받는다")
    ts_utc = ts.astimezone(timezone.utc)
    if tf is Timeframe.D1:
        return ts_utc.replace(hour=0, minute=0, second=0, microsecond=0)
    step = duration(tf)
    elapsed = ts_utc - _EPOCH
    aligned_seconds = (elapsed // step) * step
    return _EPOCH + aligned_seconds


def expected_opens(
    start: datetime, end: datetime, tf: Timeframe, sessions: list[SessionWindow]
) -> list[datetime]:
    """`[start, end)` 구간에서 `sessions` 창 안에 열리는 `tf` 캔들의
    open_time 목록(오름차순, 중복 없음).

    세션 밖 시각은 절대 만들지 않는다 — 각 세션마다 `open_at`으로 정렬된
    첫 캔들부터 `close_at` 미만까지만 순회하고, 정렬 결과가 `open_at`보다
    앞서면 다음 캔들로 건너뛴다.
    """
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("expected_opens는 tz-aware datetime만 받는다")
    step = duration(tf)
    opens: list[datetime] = []
    for session in sessions:
        cursor = align_open(session.open_at, tf)
        if cursor < session.open_at:
            cursor += step
        while cursor < session.close_at:
            if start <= cursor < end:
                opens.append(cursor)
            cursor += step
    return sorted(set(opens))
