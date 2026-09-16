"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-6 —
`analysis/resources.py` 테스트.

DoD: (1) 산정 항목 6종 각각 상한 경계값 직전(=상한, 통과)·직후(상한+1,
거부) 양쪽 단언, (2) 산정 불가 입력(DSL-4 미통과 AST·env 불일치·음수
기간·bool 기본값 int input)은 fail-closed 거부 negative test, (3) 모든
거부가 §3.3 taxonomy의 `SCRIPT_RESOURCE_LIMIT` 하나. 입력은 DSL-3 `parse()`
산출물을 쓰되, 파서가 만들 수 없는 AST(bool 기본값)만 직접 조립한다.

DEEPEN(task-2914, task-2727 감사 후속): D2 하한 보강 — 실패 주입 1(DSL-4가
타입 검사하지 않는 `OrderDecl.side` 자리로 유니온 밖 표현식이 새어 들어오는
업스트림 계약 위반), 수치 성능 단언 1(`estimate_resources` 지연,
ADR-2026-09-09-C), 게이트 적색 재현 1(`PlotDecl.style` 미방문 회귀 가드).
negative는 이미 6건으로 충분해 추가하지 않는다.
"""

from __future__ import annotations

import time

import pytest

from src.core.script.analysis.resources import (
    DEFAULT_LIMITS,
    ResourceEstimate,
    ResourceLimits,
    ScriptResourceLimitError,
    check_resources,
    enforce_limits,
    estimate_resources,
)
from src.core.script.grammar.ast import (
    CallExpr,
    Identifier,
    InputDecl,
    LetDecl,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    Program,
    TypeNode,
)
from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import ScriptTypeError, check_program

_CLOSE = "input close: series<float> = 0\n"

# ---- positive: 대표 스크립트의 산정치가 정확히 맞는다 ----


def test_estimate_of_representative_script() -> None:
    source = (
        _CLOSE
        + "input length: int = 14\n"
        + "let r = ta.rsi(close, length)\n"
        + "let m = ta.sma(close[2], 20)\n"
        + "signal go = r < 30 and m > close\n"
        + "plot(m)\n"
        + "order(buy, 1) when go"
    )
    est = check_resources(parse(source))
    # series: close, r, go(series<bool>) — m은 close[2]가 원소(float)라 스칼라(DSL-4 승격
    # 규칙) 이므로 m·plot(m)은 세지 않는다. lookback: length 14 + [2] + 20.
    assert est == ResourceEstimate(
        series_count=3, lookback_total=36, op_count=18, call_count=2, call_depth=1, plot_count=1
    )


def test_empty_program_is_zero_estimate_and_passes_default_limits() -> None:
    assert check_resources(parse("")) == ResourceEstimate()


def test_estimate_is_deterministic_and_pure() -> None:
    program = parse(_CLOSE + "let x = ta.sma(close, 5)")
    env = check_program(program)
    assert estimate_resources(program, env) == estimate_resources(program, env)
    assert program == parse(_CLOSE + "let x = ta.sma(close, 5)")  # 입력 불변


def test_let_bound_int_period_is_not_statically_folded() -> None:
    # let으로 묶인 int는 정적 접힘 대상이 아니라 0(§미검증) — 문서화된 동작.
    program = parse(_CLOSE + "let n = 30\nlet x = ta.sma(close, n)")
    assert check_resources(program).lookback_total == 0


# ---- 경계값: 각 항목 상한(통과) / 상한+1(거부) ----


def _reject(program: Program, limits: ResourceLimits, metric: str) -> None:
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(program, limits)
    assert excinfo.value.code == "SCRIPT_RESOURCE_LIMIT"
    assert excinfo.value.metric == metric
    assert metric in excinfo.value.message


def test_plot_count_boundary() -> None:
    limits = ResourceLimits(max_plots=2)
    assert check_resources(parse(_CLOSE + "plot(close)\n" * 2), limits).plot_count == 2
    _reject(parse(_CLOSE + "plot(close)\n" * 3), limits, "plot_count")


def test_series_count_boundary() -> None:
    limits = ResourceLimits(max_series=2)
    two = "input a: series<float> = 0\ninput b: series<float> = 0\n"
    assert check_resources(parse(two), limits).series_count == 2
    _reject(parse(two + "input c: series<bool> = 0"), limits, "series_count")


def test_scalar_bindings_do_not_count_as_series() -> None:
    limits = ResourceLimits(max_series=0)
    est = check_resources(parse("input n: int = 1\nlet m = n + 1\nsignal s = m > 0"), limits)
    assert est.series_count == 0


def test_lookback_total_boundary_via_postfix_index() -> None:
    limits = ResourceLimits(max_lookback_total=10)
    assert check_resources(parse(_CLOSE + "let x = close[10]"), limits).lookback_total == 10
    _reject(parse(_CLOSE + "let x = close[11]"), limits, "lookback_total")


def test_lookback_total_boundary_via_call_period() -> None:
    limits = ResourceLimits(max_lookback_total=10)
    assert check_resources(parse(_CLOSE + "let x = ta.sma(close, 10)"), limits).lookback_total == 10
    _reject(parse(_CLOSE + "let x = ta.sma(close, 11)"), limits, "lookback_total")


def test_lookback_total_boundary_via_input_default_period() -> None:
    limits = ResourceLimits(max_lookback_total=10)
    ok = _CLOSE + "input n: int = 10\nlet x = ta.sma(close, n)"
    assert check_resources(parse(ok), limits).lookback_total == 10
    _reject(parse(ok.replace("= 10", "= 11")), limits, "lookback_total")


def test_lookback_sums_across_expressions() -> None:
    est = check_resources(parse(_CLOSE + "let x = close[3] + ta.sma(close[4], 5)"))
    assert est.lookback_total == 3 + 4 + 5


def test_op_count_boundary() -> None:
    limits = ResourceLimits(max_ops=3)
    assert check_resources(parse("let x = 1 + 2"), limits).op_count == 3
    _reject(parse("let x = -(1 + 2)"), limits, "op_count")


def test_call_count_boundary() -> None:
    limits = ResourceLimits(max_calls=2)
    two = _CLOSE + "let x = ta.sma(close, 1) + ta.ema(close, 1)"
    assert check_resources(parse(two), limits).call_count == 2
    _reject(parse(two + " + ta.wma(close, 1)"), limits, "call_count")


def test_call_depth_boundary() -> None:
    limits = ResourceLimits(max_call_depth=2)
    two = _CLOSE + "let x = ta.a(ta.b(close))"
    assert check_resources(parse(two), limits).call_depth == 2
    _reject(parse(_CLOSE + "let x = ta.a(ta.b(ta.c(close)))"), limits, "call_depth")


def test_default_limits_are_the_documented_constants() -> None:
    assert DEFAULT_LIMITS == ResourceLimits(
        max_series=64,
        max_lookback_total=5000,
        max_ops=2000,
        max_calls=100,
        max_call_depth=8,
        max_plots=32,
    )


def test_enforce_limits_reports_first_exceeded_metric_in_check_order() -> None:
    est = ResourceEstimate(series_count=99, plot_count=99)
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        enforce_limits(est, DEFAULT_LIMITS)
    assert excinfo.value.metric == "series_count"


# ---- negative: 산정 불가 입력은 fail-closed 거부 ----


def test_ast_that_fails_dsl4_is_rejected_not_estimated() -> None:
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(parse("let x = y + 1"))  # 미정의 식별자 — DSL-4 미통과
    err = excinfo.value
    assert err.code == "SCRIPT_RESOURCE_LIMIT"
    assert err.metric is None
    assert isinstance(err.__cause__, ScriptTypeError)


def test_env_inconsistent_with_program_is_rejected() -> None:
    program = parse(_CLOSE + "let x = close[1]")
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        estimate_resources(program, {})  # env에 close 없음
    assert excinfo.value.metric is None


def test_negative_call_period_is_rejected_even_when_under_limit() -> None:
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(parse(_CLOSE + "let x = ta.sma(close, -5)"))
    assert excinfo.value.code == "SCRIPT_RESOURCE_LIMIT"
    assert excinfo.value.metric is None


def test_int_input_with_bool_default_used_as_period_is_rejected() -> None:
    # 파서는 bool 리터럴을 만들 수 없지만 AST는 허용한다 — DSL-1 InputDecl.value
    program = Program(
        decls=(
            InputDecl(name="close", type=TypeNode(name="series<float>"), value=0),
            InputDecl(name="n", type=TypeNode(name="int"), value=True),
            LetDecl(
                name="x",
                expr=CallExpr(
                    ns="ta", ident="sma", args=(Identifier(name="close"), Identifier(name="n"))
                ),
            ),
        )
    )
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(program)
    assert "정수가 아니라" in excinfo.value.message


def test_float_period_argument_contributes_no_lookback() -> None:
    est = check_resources(parse(_CLOSE + "let x = ta.sma(close, 10.5)"))
    assert est.lookback_total == 0


# ---- 실패 주입: DSL-4가 검사하지 않는 필드로 유니온 밖 표현식이 새는 경우 ----


class _UnknownExpr:
    """DSL-1 `Expr` 유니온 밖의 미래 표현식 종류를 흉내(업스트림 계약 위반).

    파서는 이런 값을 절대 만들지 않는다 — `model_construct`로 pydantic 검증을
    건너뛰어야만 조립할 수 있다."""


def test_unrecognized_expr_in_untyped_order_field_fails_closed() -> None:
    """실패 주입: `checker._check_decl`은 `OrderDecl`의 `when`만 `infer_type`
    하고 `side`/`qty_expr`/`opts`는 형 검사하지 않는다(§3.3 문법에 없는
    필드라 DSL-4 책임 밖). 그래서 유니온 밖 객체가 `side`에 실려도 DSL-4를
    그대로 통과할 수 있고, `estimate_resources`의 `_Acc.visit`이 마지막
    방어선이다. `side`에 `_Acc.visit`이 모르는 표현식(업스트림 계약 위반을
    흉내낸 값)을 직접 끼워 넣어, "모르니 통과"가 아니라
    `SCRIPT_RESOURCE_LIMIT`로 거부됨을 확인한다(`estimate_resources`는
    `check_program`을 다시 타지 않으므로 손으로 만든 env로 충분하다)."""
    order = OrderDecl.model_construct(
        side=_UnknownExpr(),
        qty_expr=NumberLiteral(value=1),
        opts=None,
        when=Identifier(name="go"),
    )
    program = Program.model_construct(decls=(order,))

    with pytest.raises(ScriptResourceLimitError) as excinfo:
        estimate_resources(program, {"go": "bool"})

    assert excinfo.value.code == "SCRIPT_RESOURCE_LIMIT"
    assert excinfo.value.metric is None


# ---- 성능 단언: 자원 산정 지연(ADR-2026-09-09-C) ----


def test_estimation_latency_p95_within_compile_budget_slice() -> None:
    """수치 성능 단언: ADR-2026-09-09-C 축별 성능 예산 "DSL 컴파일 300ms" 중
    자원 산정(DSL-6) 단계 몫을 30ms로 상한 잡는다(나머지는 렉스/파스/타입체크/
    lookahead 몫 — 이 리프가 전체 예산을 다 써버리면 안 된다). `env`는
    `check_program`으로 미리 얻어 DSL-4 비용을 측정에서 분리하고,
    `estimate_resources` 단독 호출 500회분(500문 스크립트 1회 순회)을
    30회 반복해 p95로 1회성 지터를 흡수한다."""
    source = _CLOSE + "\n".join(
        f"let v{i} = ta.sma(close[{i % 5}], {i % 20 + 1})" for i in range(500)
    )
    program = parse(source)
    env = check_program(program)
    budget_sec = 0.03

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        estimate_resources(program, env)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    print(f"[DSL-6 resources] 500-stmt p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


# ---- 게이트 적색 재현: PlotDecl.style 미방문 회귀 가드 ----


def test_plot_style_omission_regression_guard() -> None:
    """게이트 적색 재현: `PlotDecl.style`은 §3.3에 별도 프로덕션이 없어
    임의의 무거운 표현식(중첩 호출 등)을 담을 수 있는 `Expr`다(DSL-1
    docstring). `estimate_resources`가 `decl.style`을 방문하지 않는 (가상의)
    회귀판을 재현하면, style에 숨긴 call_depth 초과가 산정에서 통째로
    빠져 상한 검사가 통과해버린다(미탐 = 게이트가 적색이어야 할 자리에서
    녹색). 그 결여된 버전을 style을 제거한 프로그램으로 그대로 재현해
    미탐이 실제로 재현됨을 먼저 보이고, 이어서 현재 구현이 style을 방문하는
    덕에 동일 스크립트를 정확히 거부함을 확인한다(회귀 가드)."""
    limits = ResourceLimits(max_call_depth=2)
    program = parse(_CLOSE + "plot(close, ta.a(ta.b(ta.c(close))))")

    # 게이트 적색 재현: style을 뺀 버전(= 미방문 회귀)은 depth 상한 안에서 통과한다(미탐).
    style_dropped = program.model_copy(
        update={
            "decls": tuple(
                decl.model_copy(update={"style": None}) if isinstance(decl, PlotDecl) else decl
                for decl in program.decls
            )
        }
    )
    check_resources(style_dropped, limits)  # 예외 없이 통과 — 미탐 재현

    # 회귀 가드: 현재 구현은 style을 방문하므로 동일 스크립트를 정확히 거부한다.
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(program, limits)
    assert excinfo.value.metric == "call_depth"
