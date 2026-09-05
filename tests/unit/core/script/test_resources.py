"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-6 —
`analysis/resources.py` 테스트.

DoD: (1) 산정 항목 6종 각각 상한 경계값 직전(=상한, 통과)·직후(상한+1,
거부) 양쪽 단언, (2) 산정 불가 입력(DSL-4 미통과 AST·env 불일치·음수
기간·bool 기본값 int input)은 fail-closed 거부 negative test, (3) 모든
거부가 §3.3 taxonomy의 `SCRIPT_RESOURCE_LIMIT` 하나. 입력은 DSL-3 `parse()`
산출물을 쓰되, 파서가 만들 수 없는 AST(bool 기본값)만 직접 조립한다.
"""
from __future__ import annotations

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
