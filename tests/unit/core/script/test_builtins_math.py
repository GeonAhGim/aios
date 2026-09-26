"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-9a —
`runtime/builtins_math.py` 테스트.

확인 항목: (1) `math.*` 표의 함수별 스칼라 의미(round half-away-from-zero 포함),
(2) 스칼라·시리즈 승격(DSL-4 격자: 하나라도 시리즈면 시리즈), (3) na 전파·비유한
결과→na가 DSL-8 `series.py` 규칙과 같음(시드 고정 무작위 property, 독립 참조 구현
대조 — hypothesis 미설치라 `random.Random`), (4) negative: 인자 개수·bool·길이
불일치는 `BuiltinCallError`(fail-closed), (5) I-10 배선: `default_builtins()`로
파싱→IR→실행 경로에서 실제 디스패치됨.
(6) DEEPEN(task-2920) 수치 성능 단언: 시리즈 인자 빌트인 호출(브로드캐스트 포함)
1회 지연이 ADR-2026-09-09-C 백테스트 예산 하루치 몫 안에 든다.
(7) DEEPEN(task-2920) 게이트 적색 재현: `_numeric()`의 bool 거부(19~20행)가
없으면 `True`/`False`가 수치로 조용히 통과함을 먼저 보이고, 실장은 즉시 거부한다.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.ir import lower_program
from src.core.script.runtime import (
    MATH_BUILTINS,
    BuiltinCallError,
    CallSite,
    ScriptRuntimeError,
    Series,
    Value,
    default_builtins,
    execute,
)
from tests.conftest import PerfBudget

SITE = CallSite("math", "x", "float", 4)
S = Series.of_floats([1.5, -2.5, None, 4.0])


def call(ident: str, *args: Value, bars: int = 4) -> Value:
    return MATH_BUILTINS[("math", ident)](args, CallSite("math", ident, "float", bars))


# ---- (1) 함수별 스칼라 의미 ----


@pytest.mark.parametrize(
    ("ident", "args", "expected"),
    [
        ("abs", (-3,), 3.0),
        ("sign", (-0.5,), -1.0),
        ("sign", (0.0,), 0.0),
        ("sign", (7,), 1.0),
        ("floor", (2.7,), 2.0),
        ("floor", (-2.1,), -3.0),
        ("ceil", (2.1,), 3.0),
        ("round", (0.5,), 1.0),
        ("round", (-0.5,), -1.0),
        ("round", (2.5,), 3.0),
        ("round", (2.4,), 2.0),
        ("sqrt", (16,), 4.0),
        ("log", (math.e,), 1.0),
        ("exp", (0,), 1.0),
        ("pow", (2, 10), 1024.0),
        ("max", (1, 5.5, 3), 5.5),
        ("min", (1, 5.5, -3), -3.0),
        ("nz", (None,), 0.0),
        ("nz", (None, 7), 7.0),
        ("nz", (2.5, 7), 2.5),
    ],
)
def test_scalar_semantics(ident: str, args: tuple[Value, ...], expected: float) -> None:
    result = call(ident, *args)
    assert isinstance(result, float) and result == expected


def test_table_contains_exactly_the_documented_functions() -> None:
    expected = {"abs", "sign", "floor", "ceil", "round", "sqrt", "log", "exp", "pow", "max",
                "min", "nz"}  # fmt: skip
    assert {ident for ns, ident in MATH_BUILTINS if ns == "math"} == expected
    assert all(ns == "math" for ns, _ in MATH_BUILTINS)


# ---- (2) 승격 ----


def test_series_argument_promotes_result_to_series_of_bar_count() -> None:
    assert call("abs", S) == Series((1.5, 2.5, None, 4.0))
    assert call("max", S, 2) == Series((2.0, 2.0, None, 4.0))
    assert call("pow", 2, S) == Series((2**1.5, 2**-2.5, None, 16.0))
    assert call("nz", S, -1) == Series((1.5, -2.5, -1.0, 4.0))
    assert call("nz", 3, S) == Series((3.0,) * 4)
    assert call("nz", None, S) == S


def test_all_scalar_arguments_keep_scalar_result_even_with_bar_count() -> None:
    assert call("abs", -1, bars=100) == 1.0
    assert call("nz", None, 2, bars=100) == 2.0


# ---- (3) na 전파·비유한 → na ----


@pytest.mark.parametrize(
    ("ident", "args"),
    [("log", (0,)), ("log", (-1,)), ("sqrt", (-1,)), ("exp", (1000,)), ("pow", (0, -1)),
     ("pow", (-8, 0.5)), ("pow", (10, 400))],
)  # fmt: skip
def test_non_finite_or_domain_error_yields_na_not_exception(
    ident: str, args: tuple[Value, ...]
) -> None:
    assert call(ident, *args) is None


def test_na_propagates_through_every_function_except_nz() -> None:
    for _ns, ident in MATH_BUILTINS:
        if ident == "nz":
            continue
        argc = 2 if ident in ("pow", "max", "min") else 1
        assert call(ident, *([None] * argc)) is None, ident
        assert call(ident, *([1.0] * (argc - 1)), None) is None, ident
    assert call("nz", None, None) is None


_REF: dict[str, Callable[[list[float]], float]] = {
    "abs": lambda r: abs(r[0]),
    "sign": lambda r: (r[0] > 0) - (r[0] < 0),
    "floor": lambda r: math.floor(r[0]),
    "ceil": lambda r: math.ceil(r[0]),
    "round": lambda r: math.copysign(math.floor(abs(r[0]) + 0.5), r[0]),
    "sqrt": lambda r: math.sqrt(r[0]),
    "log": lambda r: math.log(r[0]),
    "exp": lambda r: math.exp(r[0]),
    "pow": lambda r: math.pow(r[0], r[1]),
    "max": max,
    "min": min,
}


def _reference(ident: str, row: list[float | None]) -> float | None:
    """독립 참조: na 하나라도 → na, 예외·비유한 → na(DSL-8 `_finite` 규칙)."""
    if any(v is None for v in row):
        return None
    try:
        out = float(_REF[ident]([v for v in row if v is not None]))
    except (ValueError, OverflowError, ZeroDivisionError):
        return None
    return out if math.isfinite(out) else None


def test_property_na_propagation_and_promotion_match_reference() -> None:
    rng = random.Random(1554)
    bars = 6

    def scalar() -> float | None:
        if rng.random() < 0.25:
            return None
        return rng.choice([rng.uniform(-50, 50), 0.0, -1.0, 1e300, rng.randint(-3, 3)])

    for _ in range(300):
        ident = rng.choice(list(_REF))
        argc = rng.randint(2, 3) if ident in ("max", "min") else (2 if ident == "pow" else 1)
        args: list[Value] = [
            Series(tuple(scalar() for _ in range(bars))) if rng.random() < 0.5 else scalar()
            for _ in range(argc)
        ]
        result = call(ident, *args, bars=bars)
        if any(isinstance(a, Series) for a in args):
            assert isinstance(result, Series) and len(result) == bars
            for t in range(bars):
                row = [a.values[t] if isinstance(a, Series) else a for a in args]
                assert result.values[t] == _reference(ident, [_f(v) for v in row]), (ident, args)
        else:
            assert result == _reference(ident, [_f(a) for a in args]), (ident, args)


def _f(v: Value) -> float | None:
    assert not isinstance(v, Series)
    return None if v is None else float(v)


# ---- (4) negative ----


@pytest.mark.parametrize(
    ("ident", "args"),
    [("abs", ()), ("abs", (1, 2)), ("pow", (2,)), ("max", (1,)), ("nz", ()), ("nz", (1, 2, 3))],
)
def test_wrong_arity_is_rejected(ident: str, args: tuple[Value, ...]) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call(ident, *args)
    assert info.value.reason == "SCRIPT_BUILTIN_ARITY"


def test_bool_operands_and_shape_mismatch_are_rejected() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("abs", True)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"
    with pytest.raises(BuiltinCallError):
        call("max", S, Series.of_bools([True, False, None, True]))
    with pytest.raises(BuiltinCallError, match="길이"):
        call("abs", Series.of_floats([1.0, 2.0]))
    with pytest.raises(ScriptRuntimeError):
        call("nz", Series.of_floats([1.0, 2.0]), 0)
    assert issubclass(BuiltinCallError, ScriptRuntimeError)


# ---- (5) 배선: 파싱→IR→execute 경로에서 default_builtins 표로 디스패치 ----


def test_wired_through_interpreter_with_default_builtins() -> None:
    src = (
        "input close: series<float> = 0\n"
        "let d = math.abs(close - 3)\n"
        "let m = math.max(close, 2.5)\n"
        "let z = math.nz(close[1], -1)\n"
        "let f = math.floor(2.7)\n"
        "signal ok = math.sqrt(close) > 1.5"
    )
    close = Series.of_floats([1, 4, 2, 9])
    result = execute(lower_program(parse(src)), bar_count=4, inputs={"close": close},
                     builtins=default_builtins())  # fmt: skip
    assert result.bindings["d"] == Series((2.0, 1.0, 1.0, 6.0))
    assert result.bindings["m"] == Series((2.5, 4.0, 2.5, 9.0))
    assert result.bindings["z"] == Series((-1.0, 1.0, 4.0, 2.0))
    assert result.bindings["f"] == 2.0
    assert result.signals["ok"] == Series((False, True, False, True))
    with pytest.raises(ScriptRuntimeError, match="미등록"):
        execute(lower_program(parse(src)), bar_count=4, inputs={"close": close})


# ---- DEEPEN(task-2920): 수치 성능 단언(빌트인 호출 지연) ----


@pytest.mark.perf
def test_series_builtin_call_latency_p95_within_backtest_budget_slice(
    perf_budget: PerfBudget,
) -> None:
    """ADR-2026-09-09-C Decision 1의 백테스트 예산(로컬 기준, 1개월 M1 1심볼 3초)
    중 빌트인 호출 1회(하루치 bar_count=1440, 1일치 1분봉에 시리즈 인자를 브로드
    캐스트하는 `apply_elementwise` 경로) 몫을 5ms로 상한한다 — DSL-8 인터프리터
    실행 예산(250ms/30-let 체인, task-2917)에서 빌트인 호출 1개가 차지할 몫에
    넉넉한 여유를 둔 수치다. 20회 반복 실행해 p95로 잰다. task-7434:
    process_time 기반 perf_budget으로 측정한다(coverage tracer 정지 포함).
    task-7673: Windows `GetProcessTimes` 해상도(15.625ms/64Hz)가 batch=4에서는
    호출당 ~3.9ms의 양자화 오차를 남겨 5ms 예산과 거의 맞닿는다 — CI 부하가
    조금만 높아도 우연히 한 틱 더 올라간 샘플이 p95를 예산 밖으로 밀어낸다.
    batch=8로 오차를 ~2ms로 더 줄여(conftest.py의 PerfBudget.sample 주석 참고,
    task-6774/task-7360과 동일 기법) 예산 대비 여유를 확보한다."""
    bar_count = 1440
    series = Series.of_floats([float(i % 97) - 48.0 for i in range(bar_count)])
    site = CallSite("math", "abs", "series<float>", bar_count)
    fn = MATH_BUILTINS[("math", "abs")]

    samples = perf_budget.samples(lambda: fn((series,), site), n=20, batch=8)
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]

    budget_ms = 5.0
    print(f"[math.abs] bar_count={bar_count} p95={p95_ms:.3f}ms budget<{budget_ms:.0f}ms")
    assert p95_ms < budget_ms


# ---- DEEPEN(task-2920): 게이트 적색 재현(bool 도메인 거부 무력화) ----


def test_disabling_bool_domain_guard_lets_true_false_compute_silently_regression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_numeric()`의 bool 거부(모듈 docstring 19~20행 "Python `bool`이 `int`의
    하위형이라 명시적으로 걸러낸다")가 무력화되는 회귀를 먼저 재현한다: bool
    검사를 지운 버전으로 바꿔치기하면 `True`/`False`가 수치로 조용히 통과해
    `math.abs(True)`가 `1.0`을 낸다(레드 — DSL-4가 bool 반환을 모델링하지 않는데도
    bool이 float 결과 계산에 섞여 들어간다). `_numeric`은 `apply_elementwise`
    안에서 이름으로(모듈 전역) 조회되므로 모듈 속성 교체가 실제 호출 경로에
    반영된다. 원래 구현은 `BuiltinCallError`(SCRIPT_BUILTIN_ARG)로 즉시 거부한다
    (회귀 가드)."""
    import src.core.script.runtime.builtins_math as builtins_math_module

    def numeric_without_bool_guard(v: object, where: str) -> float | None:
        if v is None:
            return None
        if not isinstance(v, int | float):
            raise BuiltinCallError("SCRIPT_BUILTIN_ARG", f"{where}: 수치가 아닙니다: {v!r}")
        return float(v)

    monkeypatch.setattr(builtins_math_module, "_numeric", numeric_without_bool_guard)
    assert call("abs", True) == 1.0  # 레드: bool이 수치로 조용히 통과

    monkeypatch.undo()
    with pytest.raises(BuiltinCallError) as info:
        call("abs", True)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"
