"""DC-10 `domain/aggregation/timeframe_rollup.py` — DEEPEN(task-2880,
docs/audit/DEPTH_DC_RD.md#1154) D1/D0 -> D3 증빙.

기존 test_timeframe_rollup.py는 정상계 집계·세션 경계·기본 fail-closed
음성계(길이 불일치·미정렬)를 순차 실행으로만 증명했다(D1 이하) — 감사에서
지적된 부족분(실패주입, 수치 성능 단언, 게이트 적색 재현, D3 요소)을 이
파일이 채운다. `timeframe_rollup.py`는 새 기능을 추가하지 않는다 —
여기서 "실패 주입"이란 이 순수 모듈이 실제로 위임하는 `duration()`
(LA-2)의 미등록 timeframe(`Timeframe.L2` — OHLC 개념이 없는 raw 스트림
마커)과, `rollup()`이 내부에서 유지하는 세션-open 매칭 불변이 깨졌을 때
방어 분기가 조용히 통과하지 않고 fail-closed로 거부되는지를 뜻한다.

1. 실패 주입 — OHLC 개념이 없는 `Timeframe.L2`로 롤업을 시도하면
   `UnknownTimeframeError`로 즉시 거부된다(빈 결과로 새지 않는다).
   `_session_containing`(내부 방어 분기)은 `expected_opens`가 만드는
   open이 항상 자신을 낳은 세션 창 안에 있다는 불변 덕에 공개 API
   경로로는 절대 도달하지 않는다 — white-box로 직접 호출해 그 불변이
   실제로 깨졌을 때(악성 세션 목록) 조용히 엉뚱한 세션과 짝짓지 않고
   `SessionNotFoundError`로 거부함을 증명한다.
2. 성능 단언 — 대량 M1 입력(continuous venue, 43,200행=30일)을 H1로
   롤업해도 절대시간 예산 내에 있다.
3. 게이트 적색 재현 — 적법(M5) -> 불법(M1 자기 자신) -> 적법(재조회, 결과
   불변) -> 불법(미정렬) -> 불법(길이 불일치) 순서를 재생하며, 매 불법
   호출이 앞선 적법 결과를 오염시키지 않음을 증명한다.
4. 동시 다중 인스턴스(D3) — 서로 다른 입력·타임프레임을 가진 다수의
   `rollup()` 호출을 스레드 풀에서 동시에 실행해도(순수 함수이므로 GIL
   해제 구간 없이도 안전해야 하지만, 모듈 전역 상태 유출 여부를 실제
   동시 실행으로 증명한다) 서로 결과를 오염시키지 않는다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe, Venue
from src.foundation.market_data.domain.aggregation.timeframe_rollup import (
    ROLLUP_VERSION,
    InvalidRollupTargetError,
    SessionNotFoundError,
    UnsortedCandlesError,
    rollup,
)
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError

UTC = timezone.utc


def _bitget_calendar() -> VenueCalendar:
    spec = KNOWN_SESSIONS[Venue.BITGET.value]
    return VenueCalendar(venue=Venue.BITGET.value, tz=spec.tz, regular=spec)


def _minute(i: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i)


def _minutes_rows(n: int) -> list[tuple[datetime, int, int, int, int, int, int | None]]:
    return [(_minute(i), 100 + i, 110 + i, 90 + i, 105 + i, 10, 1) for i in range(n)]


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


# ---- 실패 주입 1/2 — OHLC 개념이 없는 timeframe(L2)은 조용히 통과하지 않는다 ----


def test_rollup_to_non_ohlc_timeframe_rejected_fail_closed() -> None:
    """`Timeframe.L2`(raw L2 오더북 스트림 마커, OHLC 개념 없음)로 롤업을
    시도하면 `duration()`(LA-2)이 던지는 `UnknownTimeframeError`가 그대로
    전파돼야 한다 — 빈 `CandleColumns`나 0 채움으로 새면 안 된다."""
    columns = _m1_columns(_minutes_rows(5))
    with pytest.raises(UnknownTimeframeError):
        rollup(columns, Timeframe.L2, _bitget_calendar())


# ---- 실패 주입 2/2 — 내부 방어 분기(white-box): 악성 세션 목록은 엉뚱하게 짝짓지 않는다 ----


def test_session_containing_rejects_open_outside_every_window() -> None:
    """`_session_containing`은 `rollup()`이 `expected_opens`로 만든 open을
    그 open을 낳은 바로 그 세션 목록에서 다시 찾을 때만 쓰인다 — 정상
    경로에서는 항상 찾아지므로(그 open이 애초에 그 세션 창 안에서
    생성됐다) 공개 API로는 이 실패 분기에 절대 도달하지 않는다. 만약
    calendar 어댑터가 내부적으로 손상돼 `expected_opens`가 참조한 세션
    목록과 다른(그 open을 포함하지 않는) 목록을 넘기게 되면, 엉뚱한
    세션에 조용히 배정하는 대신 `SessionNotFoundError`로 거부돼야
    한다(방어적 불변, 직접 white-box 호출로 증명)."""
    from src.foundation.market_data.domain.aggregation import timeframe_rollup as _mod

    stray_open = _minute(0)
    unrelated_sessions = [
        SessionWindow(
            open_at=_minute(1000),
            close_at=_minute(2000),
            kind="REGULAR",
        )
    ]
    with pytest.raises(SessionNotFoundError):
        _mod._session_containing(stray_open, unrelated_sessions)


# ---- 성능 단언 ----


@pytest.mark.perf
def test_rollup_large_input_meets_latency_budget() -> None:
    """30일치 continuous venue M1(43,200행)을 H1로 롤업해도 절대시간
    예산 내에 있어야 한다 -- 두-포인터 집계가 선형 이상으로 퇴화하는
    회귀를 잡는다."""
    n = 30 * 24 * 60  # 43,200
    budget_sec = 5.0  # 실측 로컬 <1.5s
    columns = _m1_columns(_minutes_rows(n))
    calendar = _bitget_calendar()

    start = time.perf_counter()
    result = rollup(columns, Timeframe.H1, calendar)
    elapsed = time.perf_counter() - start

    print(f"[DC-10 rollup] M1 x{n} -> H1 in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert len(result.columns) == 30 * 24
    assert elapsed < budget_sec, (
        f"M1 {n}행 -> H1 롤업이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 적법/불법 호출을 섞어 재생해도 서로 오염시키지 않는다 ----


def test_gate_red_multi_step_scenario_rejects_illegal_calls_without_corrupting_legal_ones() -> None:
    """적법(M5) -> 불법(M1 자기 자신) -> 적법(같은 입력 재조회) -> 불법
    (미정렬) -> 불법(길이 불일치) 순서를 재생한다. 매 불법 호출이 정확한
    예외로 거부되고, 그 사이에 낀 적법 호출의 결과가 이전 적법 호출과
    바이트 동일함을 증명한다(순수 함수이므로 실패한 호출이 어떤 숨은
    상태도 남기지 않는다)."""
    columns = _m1_columns(_minutes_rows(10))
    calendar = _bitget_calendar()

    first_legal = rollup(columns, Timeframe.M5, calendar)
    assert first_legal.rollup_version == ROLLUP_VERSION

    # 불법 1: M1 자기 자신으로의 롤업.
    with pytest.raises(InvalidRollupTargetError):
        rollup(columns, Timeframe.M1, calendar)

    # 직전 불법 호출이 순수 함수의 결과를 오염시키지 않았다 -- 재조회해도 그대로.
    second_legal = rollup(columns, Timeframe.M5, calendar)
    assert second_legal == first_legal

    # 불법 2: 미정렬 입력.
    unsorted_rows = _minutes_rows(5)
    unsorted_rows[0], unsorted_rows[1] = unsorted_rows[1], unsorted_rows[0]
    with pytest.raises(UnsortedCandlesError):
        rollup(_m1_columns(unsorted_rows), Timeframe.M5, calendar)

    # 불법 3: 길이 불일치.
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
        rollup(broken, Timeframe.M5, calendar)

    # 앞선 두 불법 호출 이후에도 적법 결과는 여전히 처음과 바이트 동일하다.
    third_legal = rollup(columns, Timeframe.M5, calendar)
    assert third_legal == first_legal


# ---- 동시 다중 인스턴스(D3) ----


def test_concurrent_rollup_calls_do_not_cross_contaminate() -> None:
    """서로 다른 입력·타임프레임 조합을 가진 다수의 `rollup()` 호출을
    스레드 풀에서 동시에 실행해도 결과가 섞이지 않는다 -- 각 태스크는
    자신의 입력에 대응하는 결과만 받고, 순수 함수이므로 모듈 전역 상태
    (`ROLLUP_VERSION` 등)가 동시 실행 중에도 변하지 않는다."""
    calendar = _bitget_calendar()
    jobs = [
        (i, _m1_columns(_minutes_rows(20 + i)), Timeframe.M5 if i % 2 == 0 else Timeframe.M15)
        for i in range(16)
    ]

    def _run(job: tuple[int, CandleColumns, Timeframe]) -> tuple[int, object]:
        idx, cols, tf = job
        return idx, rollup(cols, tf, calendar)

    expected = {idx: rollup(cols, tf, calendar) for idx, cols, tf in jobs}

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(_run, jobs))

    assert results.keys() == expected.keys()
    for idx, result in results.items():
        assert result == expected[idx], f"job {idx}의 동시 실행 결과가 순차 실행과 다릅니다"
        assert result.rollup_version == ROLLUP_VERSION
