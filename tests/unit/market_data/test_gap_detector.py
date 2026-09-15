"""LA-5 — market_data/domain/quality/gap_detector.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §8.1, §9.2 LA-5.

핵심 케이스(§8.1): KRX 점심 없음·장 마감 후 결측은 갭 아님, 세션 중 결측
2개 → GAP 2, 크립토 24×7.

DEEPEN(task-2952, docs/audit/DEPTH_LA_LB_LC.md 410): 이 모듈은 I/O가 없는 순수
함수라 DEPTH 감사가 원래 요구한 실패주입/성능단언/게이트적색/적대적-동시성
4종을 문자 그대로는 적용할 수 없다(task-2946의 test_timeframe.py DEEPEN과 같은
전제). 아래 테스트는 그 축의 정신을 이 모듈에 맞게 옮긴 것이다: 실패주입은
이 모듈이 의존하는 내부 조회 테이블(`timeframe._DURATIONS`) 손상을 흉내내고,
성능은 대규모 세션에서 O(캔들 수)를 유지하는지, 게이트적색은 입력 순서에
흔들리지 않는 결정론을, 동시성/replay는 스레드 동시 호출의 결과 일치를
증명한다.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    SessionWindow,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain import timeframe as timeframe_module
from src.foundation.market_data.domain.calendar.known_venues import KNOWN_SESSIONS
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.market_data.domain.quality.gap_detector import detect_gaps
from src.foundation.market_data.domain.timeframe import (
    UnknownTimeframeError,
    duration,
    expected_opens,
)


def _calendar(venue: Venue) -> VenueCalendar:
    spec = KNOWN_SESSIONS[venue.value]
    return VenueCalendar(venue=venue.value, tz=spec.tz, regular=spec)


def _candle(key: SeriesKey, open_time: datetime, tf: Timeframe) -> CandleRecord:
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


def test_krx_no_lunch_break_and_after_close_missing_is_not_gap() -> None:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))  # 금요일, 정규 거래일
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    assert len(expected) >= 3  # 점심시간 결측이 껴 있었다면 연속 구간이 이보다 짧게 끊긴다
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected]
    issues = detect_gaps(candles, Timeframe.H1, sessions)
    assert issues == []  # 장 마감 이후(세션 밖)는 기대 집합에 없으므로 갭으로 잡히지 않는다


def test_krx_two_missing_candles_in_session_yield_two_gap_issues() -> None:
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    assert len(expected) >= 3
    missing = {expected[1], expected[2]}
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected if ot not in missing]
    issues = detect_gaps(candles, Timeframe.H1, sessions)
    assert len(issues) == 2
    assert {i.open_time for i in issues} == missing
    assert all(i.type is QualityIssueType.GAP and i.severity is Severity.WARN for i in issues)


def test_crypto_24x7_missing_candle_is_gap() -> None:
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M30)
    sessions = _calendar(Venue.BITGET).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.M30, sessions)
    missing_ot = expected[5]
    candles = [_candle(key, ot, Timeframe.M30) for ot in expected if ot != missing_ot]
    issues = detect_gaps(candles, Timeframe.M30, sessions)
    assert len(issues) == 1
    assert issues[0].open_time == missing_ot
    assert issues[0].type is QualityIssueType.GAP


def test_detect_gaps_empty_sessions_returns_empty() -> None:
    assert detect_gaps([], Timeframe.M1, []) == []


def test_detect_gaps_unknown_timeframe_raises() -> None:
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    with pytest.raises(UnknownTimeframeError):
        detect_gaps([], cast(Timeframe, "2h"), sessions)


# --- DEEPEN(task-2952) 실패주입: timeframe._DURATIONS 조회 테이블 손상 ------------


def test_detect_gaps_surfaces_corrupted_duration_lookup_as_extra_gaps_not_silent_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 모듈은 I/O가 없어 실 어댑터 결함을 주입할 수 없으므로, 유일하게 있을
    수 있는 결함 형태인 내부 조회 테이블(`timeframe._DURATIONS`) 손상을
    monkeypatch로 흉내낸다. 캔들은 정상 1시간 간격으로 만들었는데 조회
    테이블이 손상돼 detect_gaps가 내부에서 더 촘촘한 15분 간격으로 기대
    집합을 다시 계산하면, 정상 캔들이 새 기대 집합 대부분과 어긋나 GAP으로
    드러난다 — 빈 리스트(이슈 없음)로 조용히 통과시키지 않는다(fail-closed:
    손상 시 과소보고가 아니라 과다보고 방향으로 안전하게 실패).
    """
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected]

    monkeypatch.setitem(
        timeframe_module._DURATIONS, Timeframe.H1, timeframe_module._DURATIONS[Timeframe.M15]
    )
    issues = detect_gaps(candles, Timeframe.H1, sessions)
    assert len(issues) > 0


# --- DEEPEN(task-2952) 수치 성능 단언 ---------------------------------------------


def test_detect_gaps_completes_within_budget_for_30_days_of_m1_crypto_candles() -> None:
    """30일치 M1 24/7 캔들(43,200개 기대 open) 중 절반이 결측인 입력에서
    detect_gaps가 3초 안에 끝나고 결측 개수를 정확히 센다 — 세션마다 전체
    기대 목록을 다시 스캔하는 등 O(n^2)로 퇴행하면 이 임계값을 넘는 회귀
    가드다.
    """
    sessions = _calendar(Venue.BITGET).sessions_for(date(2026, 9, 4))
    base = sessions[0]
    long_session = SessionWindow(
        open_at=base.open_at, close_at=base.open_at + timedelta(days=30), kind=base.kind
    )
    expected = expected_opens(
        long_session.open_at, long_session.close_at, Timeframe.M1, [long_session]
    )
    assert len(expected) == 43_200
    key = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
    candles = [_candle(key, ot, Timeframe.M1) for i, ot in enumerate(expected) if i % 2 == 0]

    began = time.perf_counter()
    issues = detect_gaps(candles, Timeframe.M1, [long_session])
    elapsed = time.perf_counter() - began

    assert len(issues) == 21_600
    assert elapsed < 3.0


# --- DEEPEN(task-2952) 게이트 적색 재현(회귀 클래스) -------------------------------


def test_detect_gaps_is_order_independent_of_candle_input_order() -> None:
    """입력 캔들 리스트 순서를 무작위로 섞어도 GAP 이슈 목록은 항상 동일하다
    — 구현이 정렬된 `expected` 대신 호출자가 준 순서 그대로의 `candles`를
    기준으로 이슈를 만들도록 바뀌면 이 테스트가 적색이 된다.
    """
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    assert len(expected) >= 4
    missing = {expected[1], expected[3]}
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected if ot not in missing]

    reference = detect_gaps(candles, Timeframe.H1, sessions)
    shuffled = list(candles)
    random.Random(7).shuffle(shuffled)
    shuffled_result = detect_gaps(shuffled, Timeframe.H1, sessions)

    assert shuffled_result == reference
    assert [i.open_time for i in reference] == sorted(missing)


# --- DEEPEN(task-2952) 동시성/replay 증명 -----------------------------------------


def test_detect_gaps_is_deterministic_under_concurrent_thread_access() -> None:
    """20개 스레드가 동시에 같은 입력으로 detect_gaps를 호출해도 전부 같은
    결과를 낸다(순수 함수, replay 결정론 증명)."""
    key = SeriesKey(venue=Venue.KIS_KRX, instrument_id=uuid4(), timeframe=Timeframe.H1)
    sessions = _calendar(Venue.KIS_KRX).sessions_for(date(2026, 9, 4))
    expected = expected_opens(sessions[0].open_at, sessions[0].close_at, Timeframe.H1, sessions)
    missing = {expected[1]}
    candles = [_candle(key, ot, Timeframe.H1) for ot in expected if ot not in missing]

    reference = detect_gaps(candles, Timeframe.H1, sessions)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: detect_gaps(candles, Timeframe.H1, sessions), range(20)))
    assert all(result == reference for result in results)
