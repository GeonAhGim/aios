"""LA-2 — market_data/domain/timeframe.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-2, §8.1, §9.2 LA-2.

핵심 케이스(§8.1): `align_open` 경계(정각·D1 UTC 기준), `expected_opens`가
세션 밖 시각을 절대 만들지 않음, 알 수 없는 tf → 예외.

DEEPEN(task-2946, docs/audit/DEPTH_LA_LB_LC.md 379): 이 모듈은 I/O가 없는 순수
함수라 DEPTH 감사가 원래 요구한 실패주입/성능단언/게이트적색/적대적-동시성 4종을
문자 그대로는 적용할 수 없다(감사 §요약 "순수 도메인 함수형 리프...구조적으로
I/O가 없어 failure-injection·성능단언·게이트 재현이 성립하기 어렵다"). 아래
테스트들은 그 축의 정신을 이 모듈에 맞게 옮긴 것이다: 실패주입은 내부 조회
테이블(`_DURATIONS`) 손상을 monkeypatch로 흉내내고, 게이트적색은 이 코드가
실제로 놓칠 뻔한 회귀 클래스(비-D1 tf의 비-UTC tz 정규화, 겹치는 세션 중복
제거)를 재현하며, 동시성/replay는 순수 함수·모듈 전역 조회 테이블이 스레드
동시 접근에서도 결정론을 유지하는지로 증명한다.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from src.foundation.market_data.contracts.v1 import SessionWindow, Timeframe
from src.foundation.market_data.domain import timeframe as timeframe_module
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


# --- DEEPEN(task-2946) 실패주입: _DURATIONS 조회 테이블 손상 ---------------------


def test_align_open_fails_closed_on_corrupted_zero_duration_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_DURATIONS`가 손상돼 0 길이를 반환하면 잘못된 정렬을 조용히 내지 않고
    즉시 예외로 죽는다(fail-closed) — 이 모듈은 I/O가 없어 어댑터 결함을 주입할
    수 없으므로, 실전에서 유일하게 있을 수 있는 결함 형태인 내부 조회 테이블
    손상을 monkeypatch로 흉내낸다.
    """
    monkeypatch.setitem(timeframe_module._DURATIONS, Timeframe.M1, timedelta(0))
    with pytest.raises(ZeroDivisionError):
        align_open(datetime(2026, 9, 3, 10, 0, tzinfo=UTC), Timeframe.M1)


# --- DEEPEN(task-2946) 수치 성능 단언 --------------------------------------------


@pytest.mark.perf
def test_expected_opens_completes_within_budget_for_full_year_of_m1_candles() -> None:
    """1년치 M1 캔들(단일 24/7 세션, 525,600개 open) 전개가 3초 안에 끝난다.

    이 임계값은 알고리즘이 세션당 `open_at`~`close_at`을 캔들 스텝만큼 선형
    순회하는 현재 구현(O(캔들 수))을 지키는 회귀 가드다 — 세션마다 전체 범위를
    다시 스캔하는 등 O(n^2)로 퇴행하면 이 임계값을 넘는다.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2027, 1, 1, tzinfo=UTC)
    session = _session(start, end)

    began = time.perf_counter()
    opens = expected_opens(start, end, Timeframe.M1, [session])
    elapsed = time.perf_counter() - began

    assert len(opens) == 525_600
    assert elapsed < 3.0


# --- DEEPEN(task-2946) 게이트 적색 재현(회귀 클래스) -----------------------------


@pytest.mark.parametrize(
    ("tf", "expected_open"),
    [
        (Timeframe.M1, datetime(2026, 9, 3, 23, 30, tzinfo=UTC)),
        (Timeframe.H1, datetime(2026, 9, 3, 23, 0, tzinfo=UTC)),
    ],
)
def test_align_open_normalizes_non_utc_tz_for_intraday_timeframes(
    tf: Timeframe, expected_open: datetime
) -> None:
    """D1 경로만 비-UTC tz 정규화를 검증하던 기존 테스트가 놓친 회귀 클래스:
    `ts.astimezone(UTC)`가 D1 분기 안으로 잘못 옮겨지면 M1/H1 같은 intraday
    타임프레임은 KST 등 비-UTC 입력에서 조용히 틀린 시각에 정렬된다.
    """
    kst = timezone(timedelta(hours=9))
    ts = datetime(2026, 9, 4, 8, 30, tzinfo=kst)  # == 2026-09-03 23:30 UTC
    assert align_open(ts, tf) == expected_open


def test_expected_opens_deduplicates_overlapping_session_opens() -> None:
    """두 세션이 겹치면 겹치는 구간의 open이 두 번 나오지 않는다.

    `sorted(set(opens))`가 실수로 `sorted(opens)`(dedup 없이)로 바뀌면 이
    테스트가 적색이 된다 — 겹치는 세션은 거래소 캘린더 정정(연장/단축)에서
    실제로 발생할 수 있는 입력이라, 단일/비겹침 세션 픽스처만으로는 이 회귀를
    잡지 못한다.
    """
    session_a = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 3, 0, tzinfo=UTC),
    )
    session_b = _session(
        datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 5, 0, tzinfo=UTC),
    )
    opens = expected_opens(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 6, 0, tzinfo=UTC),
        Timeframe.H1,
        [session_a, session_b],
    )
    assert opens == [
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 1, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 2, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 3, 0, tzinfo=UTC),
        datetime(2026, 9, 3, 4, 0, tzinfo=UTC),
    ]


# --- DEEPEN(task-2946) 동시성/replay 증명 ----------------------------------------


def test_expected_opens_is_deterministic_under_concurrent_thread_access() -> None:
    """20개 스레드가 동시에 같은 입력으로 `expected_opens`를 호출해도 전부 같은
    결과를 낸다 — 이 모듈에 실제 동시-쓰기 경합은 없지만(순수 함수, 인자로만
    입력을 받음), 모듈 전역 `_DURATIONS` 조회 테이블을 여러 스레드가 동시에
    읽는 경로는 실재하므로 그 경로가 결정론을 깨지 않는지 증명한다(replay:
    반복 호출이 항상 같은 값을 냄).
    """
    session = _session(
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
    )
    args = (
        datetime(2026, 9, 3, 0, 0, tzinfo=UTC),
        datetime(2026, 9, 4, 0, 0, tzinfo=UTC),
        Timeframe.M1,
        [session],
    )
    reference = expected_opens(*args)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: expected_opens(*args), range(20)))

    assert all(result == reference for result in results)
