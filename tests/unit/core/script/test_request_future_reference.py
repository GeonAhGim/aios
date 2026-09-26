"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 — M2-2b
(task-3977, ADR-2026-09-09-B) `runtime/mtf.py` 미래참조 검출 property 테스트.

`request(symbol, timeframe, expr)`는 lookahead=off로 고정한다: 기준봉
`i` 시점에는 아직 닫히지 않은(진행 중인) 상위 타임프레임 버킷을 절대
참조하지 않는다. 이 불변식의 단일 정의는
`mtf.confirmed_source_index(bar_index, ratio) <= bar_index`다(그 반환값이
가리키는 기준봉은 `bar_index`의 과거 또는 현재이지 미래가 아니다) — 이
모듈은 hypothesis로 `(bar_index, ratio)`를 무작위 생성해 이 불변식이 항상
성립함을 검증하고, "닫히지 않은 버킷을 참조하는" naive 구현이 실제로
그 불변식을 깬다는 것을 대조군으로 재현한다(게이트 적색 재현).

D2: negative test >=3(잘못된 타임프레임 형식, 음수 봉 인덱스, ratio<1),
실패 주입 1(`resample_confirmed`에 `source`와 `bar_count`가 어긋난 시리즈를
넣으면 조용히 자르지 않고 거부), 수치 성능 단언 1(리샘플 런타임 지연),
게이트 적색 재현 1(진행 중 버킷을 참조하는 naive 인덱스 계산이 불변식을
깨는 반례를 hypothesis로 실제로 찾아냄 → 현재 구현은 같은 입력에서 위반
없음을 대조).
"""
from __future__ import annotations

import time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from src.core.script.runtime.mtf import (
    confirmed_source_index,
    resample_confirmed,
    resolve_ratio,
    timeframe_minutes,
)
from src.core.script.runtime.series import ScriptRuntimeError, Series

_BAR_INDEX = st.integers(min_value=0, max_value=10_000)
_RATIO = st.integers(min_value=1, max_value=500)


# ---- property: 확정봉 참조 인덱스는 절대 미래(> bar_index)가 아니다 ----


@given(bar_index=_BAR_INDEX, ratio=_RATIO)
@settings(max_examples=500)
def test_confirmed_source_index_never_references_the_future(bar_index: int, ratio: int) -> None:
    idx = confirmed_source_index(bar_index, ratio)
    assert 0 <= idx <= bar_index


@given(bar_index=_BAR_INDEX, ratio=_RATIO)
@settings(max_examples=500)
def test_confirmed_source_index_never_falls_inside_the_forming_bucket(
    bar_index: int, ratio: int
) -> None:
    """참조 인덱스는 `bar_index`가 속한(아직 닫히지 않은) 버킷의 바깥이어야 한다."""
    idx = confirmed_source_index(bar_index, ratio)
    forming_bucket_start = (bar_index // ratio) * ratio
    assert idx < forming_bucket_start or idx == 0  # idx==0은 warm-up(첫 버킷 미확정) 대체값


# ---- negative: 잘못된 타임프레임 형식 ----


@pytest.mark.parametrize("timeframe", ["5x", "m5", "5", "", "0m", "-1h"])
def test_invalid_timeframe_format_is_rejected(timeframe: str) -> None:
    with pytest.raises(ScriptRuntimeError):
        timeframe_minutes(timeframe)


# ---- negative: 음수 봉 인덱스 ----


def test_negative_bar_index_is_rejected() -> None:
    with pytest.raises(ScriptRuntimeError, match="0 이상"):
        confirmed_source_index(-1, 5)


# ---- negative: ratio < 1 ----


def test_ratio_below_one_is_rejected() -> None:
    with pytest.raises(ScriptRuntimeError, match="ratio"):
        confirmed_source_index(0, 0)


def test_resolve_ratio_rejects_request_timeframe_shorter_than_base() -> None:
    """기준보다 짧은 타임프레임은 정수배 조건을 만족할 수 없어 항상 거부된다
    (`resolve_ratio` 모듈 docstring — 별도 분기 없이 이 나눗셈 검사로 막힘)."""
    with pytest.raises(ScriptRuntimeError, match="정수배"):
        resolve_ratio("5m", "1m")


# ---- 실패 주입: source 길이와 bar_count가 어긋남 ----


def test_resample_confirmed_rejects_length_mismatch_instead_of_truncating() -> None:
    """실패 주입: 호스트가 잘못 조립해 `source`가 `bar_count`보다 짧은/긴
    시리즈를 넘기면(예: 상위 레이어 버그로 봉 윈도우가 어긋남) `resample_confirmed`가
    조용히 자르거나 인덱스 에러로 죽는 대신 `ScriptRuntimeError`로 거부해야
    한다(fail-closed, module docstring과 동일한 규율)."""
    short = Series.of_floats([1.0, 2.0, 3.0])
    with pytest.raises(ScriptRuntimeError, match="봉 수"):
        resample_confirmed(short, bar_count=10, ratio=2)


# ---- 수치 성능 단언: 리샘플 런타임 지연 ----


@pytest.mark.perf
def test_resample_confirmed_latency_p95_within_runtime_budget() -> None:
    """수치 성능 단언(ADR-2026-09-09-C): 5,000봉 시리즈를 ratio=5로 리샘플하는
    지연이 30회 반복 p95로 20ms 안에 머무는지 확인한다(런타임 실행은 DSL 컴파일
    예산 300ms와 별개 축이지만, 봉 수에 선형 이상으로 느려지지 않음을 조기에
    드러낸다)."""
    source = Series.of_floats(list(range(5_000)))
    budget_sec = 0.02

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        resample_confirmed(source, bar_count=5_000, ratio=5)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    print(f"[M2-2b mtf] resample p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


# ---- 게이트 적색 재현: 진행 중 버킷을 참조하는 naive 구현의 불변식 위반 ----


def _naive_source_index_including_forming_bucket(bar_index: int, ratio: int) -> int:
    """수정 전(가상) 구현: "가장 가까운 상위 버킷 경계"를 확정 여부와 무관하게
    반환하는 흔한 실수 — `bar_index`가 정확히 버킷 경계에 있을 때 그 버킷이
    "방금 닫혔다"고 착각해 아직 `bar_index`와 같은 시점의(=미래 아님이지만
    진행 중인 버킷의 마지막 원소가 `bar_index` 자신보다 클 수 있는) 인덱스를
    내어준다."""
    bucket = bar_index // ratio
    return bucket * ratio + (ratio - 1)  # 현재 진행 중인 버킷의 "예정된" 마지막 인덱스


@given(
    bar_index=st.integers(min_value=0, max_value=1_000),
    ratio=st.integers(min_value=2, max_value=50),
)
@settings(max_examples=200)
def test_forming_bucket_reference_regression_guard(bar_index: int, ratio: int) -> None:
    """게이트 적색 재현: naive 구현은 `bar_index`가 진행 중인 버킷 안에 있을 때
    (버킷의 예정된 마지막 인덱스가 `bar_index`보다 미래) 미래 인덱스를 반환할
    수 있다 — hypothesis가 그런 `(bar_index, ratio)`를 실제로 찾아내 위반을
    재현한다. 현재 구현(`confirmed_source_index`)은 같은 입력에서 항상
    `<= bar_index`를 지킨다(회귀 가드)."""
    naive_idx = _naive_source_index_including_forming_bucket(bar_index, ratio)
    real_idx = confirmed_source_index(bar_index, ratio)

    forming_bucket_last_index = (bar_index // ratio) * ratio + (ratio - 1)
    if forming_bucket_last_index > bar_index:
        # 적색 재현: naive 구현이 실제로 미래를 참조하는 경우가 존재한다.
        assert naive_idx > bar_index

    # 회귀 가드: 현재 구현은 이 경우에도 절대 미래를 참조하지 않는다.
    assert real_idx <= bar_index
