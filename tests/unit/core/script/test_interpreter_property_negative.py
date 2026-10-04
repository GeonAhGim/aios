"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 DoD — negative /
failure-injection / red-gate / 성능 단언.

부록 파일: test_interpreter_property.py의 핵심 property(생성기·참조 구현·비교)와
REGISTRY/_check_program을 공유하며, 불변식 위반 입력 거부·의존성 예외 전파·
회귀 재현·성능 예산 검사를 담는다.
"""

from __future__ import annotations

from typing import cast

import pytest

from src.core.script.runtime import (
    CallSite,
    ScriptRuntimeError,
    Series,
    Value,
    broadcast,
    execute,
)

from .test_interpreter_property import (
    BARS,
    REGISTRY,
    _check_program,
    _minimal_ir,
)

# ---- negative(불변식 위반 입력 거부) ----


def test_execute_rejects_series_input_shorter_than_bar_count() -> None:
    """negative(DEEPEN task-4146): 호스트가 준 시리즈 입력 길이가 `bar_count`와
    다르면 조용히 자르거나 채우지 않고 `ScriptRuntimeError`로 거부한다
    (`runtime/series.py` 모듈 docstring: "길이가 다른 시리즈끼리의 연산은
    ScriptRuntimeError")."""
    ir = _minimal_ir()
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=BARS, inputs={"close": Series.of_floats([1.0, 2.0])})


def test_execute_rejects_missing_required_series_input() -> None:
    """negative(DEEPEN task-4146): 시리즈 입력은 리터럴 기본값을 시리즈로 펴지
    않는다 — 호스트가 아예 공급하지 않으면 `ScriptRuntimeError`(interpreter.py
    `_declare_input`)."""
    ir = _minimal_ir()
    with pytest.raises(ScriptRuntimeError):
        execute(ir, bar_count=BARS, inputs={})


def test_execute_rejects_unknown_input_name() -> None:
    """negative(DEEPEN task-4146): 선언되지 않은 입력 이름이 `inputs`에 섞여
    있으면(오타) 조용히 무시하지 않고 거부한다."""
    ir = _minimal_ir()
    with pytest.raises(ScriptRuntimeError):
        execute(
            ir,
            bar_count=BARS,
            inputs={"close": Series.of_floats([1.0] * BARS), "typo": 1},
        )


def test_execute_rejects_builtin_return_value_shorter_than_bar_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative(DEEPEN task-4146): 빌트인 반환값도 봉 수와 대조된다(외부 코드의
    산출물을 신뢰하지 않는다 — interpreter.py 모듈 docstring). 레지스트리
    항목을 봉 수보다 짧은 시리즈를 내도록 몽키패치하면 `execute`가 그 값을
    그대로 흘려보내지 않고 거부해야 한다."""

    def _short_sma(args: tuple[Value, ...], site: CallSite) -> Value:
        return Series.of_floats([1.0] * (site.bar_count - 1))

    monkeypatch.setitem(REGISTRY, ("ta", "sma"), _short_sma)
    hit = False
    for seed in range(30):
        try:
            _check_program(seed)
        except ScriptRuntimeError:
            hit = True
            break
        except AssertionError:
            continue
    assert hit, "no seed among the first 30 exercised ta.sma to trigger the guard"


# ---- 실패주입(의존성 예외 전파) ----


def test_execute_propagates_exception_raised_by_broken_builtin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입(DEEPEN task-4146): 빌트인 본체(DSL-9 소유)가 내부적으로 예외를
    던지면 인터프리터가 그것을 삼키거나 na로 바꿔치기하지 않고 그대로
    전파해야 한다(fail-closed — "조용한 기본값 없음", interpreter.py
    `ScriptRuntimeError` 클래스 docstring). 의존성 실패를 흉내내려고
    `ta.sma`를 항상 예외를 던지는 레지스트리 항목으로 몽키패치한다."""

    def _boom(args: tuple[Value, ...], site: CallSite) -> Value:
        raise ZeroDivisionError("simulated builtin dependency failure")

    monkeypatch.setitem(REGISTRY, ("ta", "sma"), _boom)
    hit = False
    for seed in range(30):
        try:
            _check_program(seed)
        except ZeroDivisionError:
            hit = True
            break
        except AssertionError:
            continue
    assert hit, "no seed among the first 30 exercised ta.sma to trigger the injected failure"


# ---- red-gate 재현(회귀가 실제로 이 게이트를 붉게 만드는지) ----


def test_property_suite_catches_an_off_by_one_sma_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """red-gate 재현(DEEPEN task-4146): `ta.sma`가 창을 한 봉 미래로 미끄러뜨리는
    회귀(look-ahead 버그, 백테스트=라이브 산출물 공유 I-05 위반 방향)를 주입하면
    참조 구현과의 비교가 실제로 실패해야 한다 — 이 property 스위트가 그런
    회귀를 통과시키지 않는다는 증거."""

    def _v_sma_off_by_one(args: tuple[Value, ...], site: CallSite) -> Value:
        src, n = broadcast(args[0], site.bar_count).values, args[1]
        assert isinstance(n, int) and n >= 1
        out: list[float | None] = []
        for t in range(site.bar_count):
            future_t = t + 1
            window = (
                src[future_t - n + 1 : future_t + 1] if n - 1 <= future_t < site.bar_count else ()
            )
            ok = bool(window) and len(window) == n and all(w is not None for w in window)
            out.append(sum(cast(tuple[float, ...], window)) / n if ok else None)
        return Series.of_floats(out)

    monkeypatch.setitem(REGISTRY, ("ta", "sma"), _v_sma_off_by_one)
    hit = False
    for seed in range(30):
        try:
            _check_program(seed)
        except AssertionError:
            hit = True
            break
    assert hit, "look-ahead 회귀를 주입했는데도 어떤 seed도 잡아내지 못했다"


# ---- 성능 단언 ----


@pytest.mark.perf
def test_property_suite_runs_within_latency_budget() -> None:
    """성능 단언(ADR-2026-09-09-C D2, DEEPEN task-4146): 150개 시드 전체를
    순차 실행해도 예산(3초) 안에 끝나야 한다 — 참조 구현 대조가 우발적으로
    seed당 재귀 폭주(예: memo 미적용)를 일으키지 않는지 확인하는 회귀
    가드. 측정치는 로컬 기준 ~0.2s(14배 여유)."""
    import time

    started = time.perf_counter()
    for seed in range(150):
        _check_program(seed)
    elapsed = time.perf_counter() - started
    assert elapsed < 3.0, f"property suite took {elapsed:.4f}s, exceeds 3s budget"
