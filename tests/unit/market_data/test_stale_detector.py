"""LA-5 — market_data/domain/quality/stale_detector.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-5, §8.1, §9.2 LA-5.

핵심 케이스(§8.1): 세션 밖 스테일 아님, 세션 내 `3×duration+1s` STALE.

DEEPEN(task-2952, docs/audit/DEPTH_LA_LB_LC.md 410): 이 모듈은 I/O가 없는 순수
함수라 DEPTH 감사가 원래 요구한 실패주입/성능단언/게이트적색/적대적-동시성
4종을 문자 그대로는 적용할 수 없다(task-2946의 test_timeframe.py DEEPEN과 같은
전제). 아래 테스트는 그 축의 정신을 이 모듈에 맞게 옮긴 것이다: 실패주입은
이 모듈이 의존하는 내부 조회 테이블(`timeframe._DURATIONS`) 손상을 흉내내고,
성능은 대량 호출에서 호출당 O(1)을 유지하는지, 게이트적색은 서로 다른
tzinfo·threshold=0 경계에서의 회귀 클래스를, 동시성/replay는 스레드 동시
호출의 결과 일치(및 독립된 객체 생성)를 증명한다.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import cast

import pytest

from src.foundation.market_data.contracts.v1 import QualityIssueType, Severity, Timeframe
from src.foundation.market_data.domain import timeframe as timeframe_module
from src.foundation.market_data.domain.quality.stale_detector import detect_stale
from src.foundation.market_data.domain.timeframe import UnknownTimeframeError, duration

UTC = timezone.utc
_LAST = datetime(2026, 9, 3, 10, 0, tzinfo=UTC)


def test_session_closed_never_stale_regardless_of_elapsed() -> None:
    now = _LAST + timedelta(days=1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=False) is None


def test_session_open_exceeds_3x_duration_plus_1s_is_stale() -> None:
    now = _LAST + 3 * duration(Timeframe.M1) + timedelta(seconds=1)
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True)
    assert issue is not None
    assert issue.type is QualityIssueType.STALE
    assert issue.severity is Severity.WARN
    assert issue.open_time == _LAST


def test_session_open_exactly_at_threshold_is_not_stale() -> None:
    now = _LAST + 3 * duration(Timeframe.M1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=True) is None


def test_custom_k_changes_threshold() -> None:
    now = _LAST + 2 * duration(Timeframe.M1) + timedelta(seconds=1)
    assert detect_stale(_LAST, now, Timeframe.M1, session_open=True, k=3) is None
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True, k=2)
    assert issue is not None
    assert issue.type is QualityIssueType.STALE


def test_naive_last_ts_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        detect_stale(datetime(2026, 9, 3, 10, 0), _LAST, Timeframe.M1, session_open=True)


def test_naive_now_rejected() -> None:
    with pytest.raises(ValueError, match="tz-aware"):
        detect_stale(_LAST, datetime(2026, 9, 3, 10, 5), Timeframe.M1, session_open=True)


def test_unknown_timeframe_raises() -> None:
    with pytest.raises(UnknownTimeframeError):
        detect_stale(_LAST, _LAST, cast(Timeframe, "2h"), session_open=True)


# --- DEEPEN(task-2952) 실패주입: timeframe._DURATIONS 조회 테이블 손상 ------------


def test_detect_stale_fails_closed_when_duration_lookup_is_corrupted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 모듈은 I/O가 없어 실 어댑터 결함을 주입할 수 없으므로, 유일하게 있을
    수 있는 결함 형태인 내부 조회 테이블(`timeframe._DURATIONS`) 손상을
    monkeypatch로 흉내낸다. 조회 테이블이 손상돼 duration이 0으로 반환되면
    threshold도 0이 되어, 아주 작은 경과(1마이크로초)조차 STALE로 드러난다
    — 손상된 임계값 때문에 조용히 모든 경과를 통과시키는(fail-open) 대신
    과다보고 방향으로 안전하게 실패한다.
    """
    monkeypatch.setitem(timeframe_module._DURATIONS, Timeframe.M1, timedelta(0))
    now = _LAST + timedelta(microseconds=1)
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True)
    assert issue is not None
    assert issue.type is QualityIssueType.STALE


# --- DEEPEN(task-2952) 수치 성능 단언 ---------------------------------------------


def test_detect_stale_handles_100k_calls_within_budget() -> None:
    """100,000회 연속 호출이 2초 안에 끝난다 — 호출당 O(1)을 유지하는 회귀
    가드다(우발적으로 무거운 연산·I/O가 끼어들면 이 임계값을 넘는다).
    """
    now = _LAST + 2 * duration(Timeframe.M1)
    began = time.perf_counter()
    for _ in range(100_000):
        detect_stale(_LAST, now, Timeframe.M1, session_open=True)
    elapsed = time.perf_counter() - began
    assert elapsed < 2.0


# --- DEEPEN(task-2952) 게이트 적색 재현(회귀 클래스) -------------------------------


def test_detect_stale_computes_elapsed_correctly_across_differing_tzinfo() -> None:
    """last_ts는 KST, now는 UTC처럼 서로 다른 tzinfo 객체로 들어와도 실제
    경과가 올바르게 계산된다 — 벽시계 필드(hour 등)만 비교하는 식으로
    퇴행하면 이 테스트가 적색이 된다.
    """
    kst = timezone(timedelta(hours=9))
    last_ts_kst = _LAST.astimezone(kst)  # 같은 순간, 다른 tzinfo 객체
    now_at_threshold = _LAST + 3 * duration(Timeframe.M1)
    assert detect_stale(last_ts_kst, now_at_threshold, Timeframe.M1, session_open=True) is None

    now_past_threshold = now_at_threshold + timedelta(seconds=1)
    issue = detect_stale(last_ts_kst, now_past_threshold, Timeframe.M1, session_open=True)
    assert issue is not None
    assert issue.open_time == last_ts_kst


def test_detect_stale_zero_k_flags_any_positive_elapsed() -> None:
    """k=0이면 threshold가 0이 되어, 아주 작은 경과라도 STALE로 잡힌다 —
    threshold 0을 경계 비교·나눗셈에서 특별취급하다 예외를 내거나 조용히
    통과시키는 회귀를 잡는다.
    """
    now = _LAST + timedelta(microseconds=1)
    issue = detect_stale(_LAST, now, Timeframe.M1, session_open=True, k=0)
    assert issue is not None
    assert issue.detail["threshold_s"] == "0.0"


# --- DEEPEN(task-2952) 동시성/replay 증명 -----------------------------------------


def test_detect_stale_is_deterministic_under_concurrent_thread_access() -> None:
    """20개 스레드가 동시에 같은 입력으로 detect_stale을 호출해도 모두 같은
    결과(detail 문자열 포함)를 낸다 — 그리고 각 호출은 독립된 QualityIssue
    객체를 만든다(스레드 간 캐시된 객체를 공유하지 않는다).
    """
    now = _LAST + 3 * duration(Timeframe.M1) + timedelta(seconds=1)
    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(
            pool.map(lambda _: detect_stale(_LAST, now, Timeframe.M1, session_open=True), range(20))
        )
    assert all(r is not None for r in results)
    assert all(r == results[0] for r in results)
    assert len({id(r) for r in results}) == 20
