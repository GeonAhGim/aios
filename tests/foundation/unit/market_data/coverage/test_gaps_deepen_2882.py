"""DC-7 `domain/coverage/gaps.py` — DEEPEN(task-2882,
docs/audit/DEPTH_DC_RD.md#1178) D1 -> D3 증빙.

기존 test_gaps.py는 세 갈래 판정(NOT_COVERED/MISSING_CANDLES/휴장은 갭
아님)과 fail-closed negative 4건, 결정론 정렬을 순차 실행으로 증명했다
(D1 수준). `gaps.py`는 순수 함수(I/O 없음)라 감사가 지적한 부족분(실패
주입, 수치 성능 단언, 게이트 적색 재현, D3 요소)을 이 파일이 채운다 —
`gaps.py`는 새 기능 없이 task-1178 그대로 무수정 유지한다.

1. 실패 주입 — (a) OHLC 개념이 없는 `Timeframe.L2`로 `plan_fetch`를
   호출하면 `duration()`(LA-2)이 던지는 `UnknownTimeframeError`가 그대로
   전파되는 것, (b) `spans`/`candles`의 `Sequence[...]` 타입힌트는
   런타임을 강제하지 않으므로 비-`CoverageSpan`/비-`CandleRecord` 요소가
   섞여도 조용히 틀린(과소) 갭 목록을 내는 대신 즉시 `AttributeError`로
   크래시하는 것, (c) `calendar`(LA-3) 어댑터 자체가 죽으면(`sessions_for`
   예외) 빈 갭 목록("완전히 커버됨")으로 새지 않고 예외가 그대로
   전파되는 것을 white-box 페이크 calendar로 증명한다.
2. 성능 단언 — 400일치(9,600행) H1 대량 캔들·구간 대조가 절대시간 예산
   내에 있다.
3. 게이트 적색 재현 — 적법 -> 불법(L2) -> 적법(재조회, 결과 불변) ->
   불법(naive datetime) -> 불법(역전 구간) -> 적법 순서를 재생해도 순수
   함수이므로 실패한 호출이 직전 적법 결과를 오염시키지 않는다.
4. 동시 다중 인스턴스(D3) — 서로 다른 구간·커버리지 조합의 `plan_fetch`
   호출을 스레드 풀에서 동시 실행해도 결과가 섞이지 않는다(모듈 전역
   가변 상태 없음의 동시성 증거).
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.coverage import CoverageSpan, QualityGrade
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.coverage.gaps import (
    CoverageGap,
    GapReason,
    IndeterminateCoverageError,
    plan_fetch,
)
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError, duration

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _calendar(venue: Venue = Venue.BITGET) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _dt(day: int, hour: int = 0) -> datetime:
    return datetime(2026, 1, day, hour, 0, tzinfo=timezone.utc)


def _span(*, start_at: datetime, end_at: datetime, tf: Timeframe = Timeframe.H1) -> CoverageSpan:
    return CoverageSpan(
        instrument_id=_ULID,
        venue=Venue.BITGET,
        asset_class=AssetClass.CRYPTO,
        timeframe=tf,
        quality_grade=QualityGrade.RAW,
        start_at=start_at,
        end_at=end_at,
    )


def _candle(open_time: datetime, tf: Timeframe = Timeframe.H1) -> CandleRecord:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=tf)
    return CandleRecord(
        key=key,
        open_time=open_time,
        close_time=open_time + duration(tf),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )


# ---- 실패 주입 ----


def test_unknown_timeframe_propagates_instead_of_silent_empty_gaps() -> None:
    """`Timeframe.L2`(raw L2 오더북 스트림 마커, OHLC 개념 없음)로 갭 판정을
    시도하면 `duration()`이 던지는 `UnknownTimeframeError`가 그대로
    전파돼야 한다 — "갭 없음"으로 새면 커버리지가 충분하다고 오판한다."""
    with pytest.raises(UnknownTimeframeError):
        plan_fetch(
            spans=[],
            candles=[],
            tf=Timeframe.L2,
            calendar=_calendar(),
            range_start=_dt(1),
            range_end=_dt(2),
        )


def test_non_coverage_span_element_crashes_instead_of_silent_wrong_result() -> None:
    """`Sequence[CoverageSpan]` 타입힌트는 런타임을 강제하지 않는다 —
    역직렬화 경로 등에서 딕셔너리가 섞여 들어오면 조용히 건너뛰거나 틀린
    갭 목록을 내는 대신 즉시 크래시해야 한다(fail-closed)."""
    valid = _span(start_at=_dt(1), end_at=_dt(2))
    garbage: Any = {"start_at": _dt(1), "end_at": _dt(2)}
    with pytest.raises(AttributeError):
        plan_fetch(
            spans=[valid, garbage],
            candles=[],
            tf=Timeframe.H1,
            calendar=_calendar(),
            range_start=_dt(1),
            range_end=_dt(2),
        )


def test_none_candle_element_crashes_instead_of_silent_wrong_result() -> None:
    span = _span(start_at=_dt(1), end_at=_dt(2))
    with pytest.raises(AttributeError):
        plan_fetch(
            spans=[span],
            candles=cast(Any, [_candle(_dt(1, 0)), None]),
            tf=Timeframe.H1,
            calendar=_calendar(),
            range_start=_dt(1),
            range_end=_dt(2),
        )


class _CrashingCalendar:
    """LA-3 calendar 어댑터가 내부적으로 죽는 상황(설정 손상·미지원 거래일
    조회 등)을 흉내낸다. `gaps.py`는 `VenueCalendar`를 duck-typing으로만
    쓰므로(`.venue`/`.trading_day_of`/`.sessions_for`), 이 페이크로도 동일
    경로를 white-box 재현할 수 있다."""

    venue = Venue.BITGET.value

    def trading_day_of(self, at: datetime) -> date:
        return at.date()

    def sessions_for(self, day: date) -> list[Any]:
        raise RuntimeError("calendar adapter 손상 시뮬레이션")


def test_calendar_adapter_crash_propagates_instead_of_empty_gaps() -> None:
    """calendar 어댑터가 세션 조회 중 죽으면, `plan_fetch`는 "세션 없음 ->
    갭 없음"으로 흡수하지 않고 예외를 그대로 전파해야 한다 — 흡수하면
    실제로는 판정 불가인데 "완전히 커버됨"으로 보고하게 된다."""
    with pytest.raises(RuntimeError):
        plan_fetch(
            spans=[],
            candles=[],
            tf=Timeframe.H1,
            calendar=cast(Any, _CrashingCalendar()),
            range_start=_dt(1),
            range_end=_dt(2),
        )


# ---- 성능 단언 ----


@pytest.mark.perf
def test_plan_fetch_meets_latency_budget_for_large_continuous_range() -> None:
    """400일치(9,600시간) BITGET(continuous) H1 구간에서, 절반은 캔들이
    비어 결측(MISSING_CANDLES)인 대량 대조도 절대시간 예산 내에 있어야
    한다 — 회귀가 있다면 선형 스캔이 제곱으로 퇴화했는지 확인한다."""
    days = 400
    range_start = _dt(1)
    range_end = range_start + timedelta(days=days)
    span = _span(start_at=range_start, end_at=range_end)
    candles = [
        _candle(range_start + timedelta(hours=h))
        for h in range(days * 24)
        if h % 2 == 0  # 절반만 존재 -> 나머지는 MISSING_CANDLES
    ]

    budget_sec = 5.0  # 실측 로컬 예산(회귀 감지용 여유 포함)
    start = time.perf_counter()
    gaps = plan_fetch(
        spans=[span],
        candles=candles,
        tf=Timeframe.H1,
        calendar=_calendar(),
        range_start=range_start,
        range_end=range_end,
    )
    elapsed = time.perf_counter() - start

    print(
        f"[DC-7 gaps] {days}d({days * 24}h) H1 대조 -> {len(gaps)}개 갭 in "
        f"{elapsed:.3f}s (budget<{budget_sec}s)"
    )
    assert all(g.reason == GapReason.MISSING_CANDLES for g in gaps)
    assert elapsed < budget_sec, (
        f"{days}일 H1 대조가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 ----


def test_gate_red_multi_step_scenario_rejects_illegal_calls_without_corrupting_legal_ones() -> None:
    """적법 -> 불법(L2) -> 적법(같은 입력 재조회) -> 불법(naive datetime)
    -> 불법(역전 구간) -> 적법 순서를 재생한다. 매 불법 호출이 정확한
    예외로 거부되고, 그 사이에 낀 적법 호출의 결과가 이전 적법 호출과
    바이트 동일함을 증명한다(순수 함수이므로 실패한 호출이 숨은 상태를
    남기지 않는다)."""
    span = _span(start_at=_dt(1), end_at=_dt(1, 4))
    candles = [_candle(_dt(1, h)) for h in (0, 1, 3)]
    calendar = _calendar()

    def _call(tf: Timeframe, range_start: datetime, range_end: datetime) -> Sequence[CoverageGap]:
        return plan_fetch(
            spans=[span],
            candles=candles,
            tf=tf,
            calendar=calendar,
            range_start=range_start,
            range_end=range_end,
        )

    first_legal = _call(Timeframe.H1, _dt(1), _dt(1, 6))
    assert first_legal == [
        CoverageGap(_dt(1, 2), _dt(1, 3), GapReason.MISSING_CANDLES),
        CoverageGap(_dt(1, 4), _dt(1, 6), GapReason.NOT_COVERED),
    ]

    # L2: spans가 H1이므로 duration()보다 먼저 축 검증(_validate_axis)에서
    # timeframe 불일치로 걸린다 -- 여전히 fail-closed 거부다.
    with pytest.raises(IndeterminateCoverageError):
        _call(Timeframe.L2, _dt(1), _dt(1, 6))

    second_legal = _call(Timeframe.H1, _dt(1), _dt(1, 6))
    assert second_legal == first_legal

    with pytest.raises(IndeterminateCoverageError):
        _call(Timeframe.H1, datetime(2026, 1, 1, 0, 0), _dt(1, 6))

    with pytest.raises(IndeterminateCoverageError):
        _call(Timeframe.H1, _dt(1, 6), _dt(1))

    third_legal = _call(Timeframe.H1, _dt(1), _dt(1, 6))
    assert third_legal == first_legal


# ---- 동시 다중 인스턴스(D3) ----


def _scenario(i: int) -> dict[str, Any]:
    range_start = _dt(1) + timedelta(days=i)
    range_end = range_start + timedelta(hours=6)
    span = _span(start_at=range_start, end_at=range_start + timedelta(hours=3 + (i % 3)))
    candles = [_candle(range_start + timedelta(hours=h)) for h in range(3 + (i % 3)) if h != i % 3]
    return {
        "spans": [span],
        "candles": candles,
        "tf": Timeframe.H1,
        "calendar": _calendar(),
        "range_start": range_start,
        "range_end": range_end,
    }


def test_concurrent_plan_fetch_calls_do_not_cross_contaminate() -> None:
    """서로 다른 구간·커버리지 조합을 가진 다수의 `plan_fetch` 호출을
    스레드 풀에서 동시에 실행해도 결과가 섞이지 않는다 — 각 태스크는
    자신의 입력에 대응하는 결과만 받는다(순수 함수, 모듈 전역 상태 없음
    의 동시성 증거)."""
    jobs = [(i, _scenario(i)) for i in range(20)]
    expected = {idx: plan_fetch(**scenario) for idx, scenario in jobs}

    def _run(job: tuple[int, dict[str, Any]]) -> tuple[int, Sequence[CoverageGap]]:
        idx, scenario = job
        return idx, plan_fetch(**scenario)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(_run, jobs))

    assert results.keys() == expected.keys()
    for idx, result in results.items():
        assert result == expected[idx], f"job {idx}의 동시 실행 결과가 순차 실행과 다릅니다"
