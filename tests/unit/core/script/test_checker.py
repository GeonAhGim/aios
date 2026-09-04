"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-4 —
`typing/types.py` + `typing/checker.py` 테스트.

DoD("시리즈/스칼라 승격·거부")를 두 축으로 확인한다: (1) 선언(input/let/
signal)이 환경에 올바른 타입으로 등록되고 이후 decl에서 재사용되는지,
(2) 표현식 트리 전체(단항·이항·postfix·call)에서 승격이 성립하는 조합은
통과하고 성립하지 않는 조합(bool과 수치의 혼용, 스칼라 인덱싱 등)은
`ScriptTypeError`(`SCRIPT_TYPE`)로 거부되는지. 입력은 항상 DSL-3
`parse()`의 실제 산출물(AST를 손으로 조립하지 않는다)이라 파서·검사기
경계가 실제로 맞물리는지도 함께 검증한다.
"""
from __future__ import annotations

import pytest

from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import ScriptTypeError, check_program

# ---- 선언 등록: input/let/signal이 환경에 타입으로 남는다 ----


def test_input_scalar_registers_declared_type() -> None:
    env = check_program(parse("input length: int = 14"))
    assert env == {"length": "int"}


def test_input_series_registers_declared_type() -> None:
    env = check_program(parse("input src: series<float> = 0"))
    assert env == {"src": "series<float>"}


def test_let_infers_type_from_expr() -> None:
    env = check_program(parse("let a = 1\nlet b = a + 2"))
    assert env == {"a": "int", "b": "int"}


def test_signal_registers_bool_family_type() -> None:
    env = check_program(parse("signal go_long = 1 < 2"))
    assert env == {"go_long": "bool"}


def test_redeclaration_is_script_type_error() -> None:
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse("let a = 1\nlet a = 2"))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_undefined_identifier_is_script_type_error() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("let x = y + 1"))


# ---- 단항: 산술 부정은 수치만, 논리 부정은 bool만 ----


def test_unary_minus_on_numeric_passes_through_type() -> None:
    env = check_program(parse("let a = 1\nlet b = -a"))
    assert env["b"] == "int"


def test_unary_minus_on_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = -g"))


def test_not_on_bool_passes_through_type() -> None:
    env = check_program(parse("signal g = not (1 < 2)"))
    assert env["g"] == "bool"


def test_not_on_numeric_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("let bad = not 1"))


# ---- postfix: 시리즈만 인덱싱 가능, 원소 타입으로 투영 ----


def test_postfix_index_on_series_projects_to_element_type() -> None:
    env = check_program(parse("input src: series<float> = 0\nlet a = src[1]"))
    assert env["a"] == "float"


def test_postfix_index_on_scalar_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("input x: int = 1\nlet bad = x[0]"))


# ---- 산술(+·-·*·/): int/float/series<float> 승격, bool과는 거부 ----


def test_arith_int_and_int_stays_int() -> None:
    env = check_program(parse("let a = 1 + 2"))
    assert env["a"] == "int"


def test_arith_int_and_float_promotes_to_float() -> None:
    env = check_program(parse("let a = 1 + 1.5"))
    assert env["a"] == "float"


def test_arith_scalar_and_series_promotes_to_series() -> None:
    env = check_program(
        parse("input src: series<float> = 0\nlet a = src + 1")
    )
    assert env["a"] == "series<float>"


def test_arith_with_bool_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = g + 1"))


# ---- 비교(cmp·crosses_*): 수치만, 결과는 시리즈 섞이면 series<bool> ----


def test_cmp_of_scalars_is_bool() -> None:
    env = check_program(parse("signal g = 1 < 2"))
    assert env["g"] == "bool"


def test_cmp_of_series_is_series_bool() -> None:
    env = check_program(
        parse("input src: series<float> = 0\nsignal g = src > 0")
    )
    assert env["g"] == "series<bool>"


def test_crosses_above_of_two_series_is_series_bool() -> None:
    env = check_program(
        parse(
            "input a: series<float> = 0\n"
            "input b: series<float> = 0\n"
            "signal cross = a crosses_above b"
        )
    )
    assert env["cross"] == "series<bool>"


def test_cmp_with_bool_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nsignal bad = g < 1"))


# ---- 논리(and·or): bool 계열만, series<bool>이 섞이면 승격 ----


def test_logical_and_of_bools_is_bool() -> None:
    env = check_program(parse("signal g = (1 < 2) and (2 < 3)"))
    assert env["g"] == "bool"


def test_logical_and_promotes_to_series_bool() -> None:
    env = check_program(
        parse(
            "input src: series<float> = 0\n"
            "signal s1 = src > 0\n"
            "signal s2 = src < 100\n"
            "signal both = s1 and s2"
        )
    )
    assert env["both"] == "series<bool>"


def test_logical_and_with_numeric_operand_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal bad = 1 and 2"))


# ---- call(ns.ident): 인자는 수치만, 시리즈가 하나라도 있으면 series<float> ----


def test_call_all_scalar_args_returns_float() -> None:
    env = check_program(parse("let x = math.pi()"))
    assert env["x"] == "float"


def test_call_with_series_arg_returns_series_float() -> None:
    env = check_program(
        parse("input close: series<float> = 0\nlet rsi_val = ta.rsi(close, 14)")
    )
    assert env["rsi_val"] == "series<float>"


def test_call_with_bool_arg_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal g = 1 < 2\nlet bad = ta.sma(g, 14)"))


# ---- plot/order: 각자의 위치 제약 ----


def test_plot_of_numeric_passes() -> None:
    check_program(parse("input src: series<float> = 0\nplot(src)"))


def test_plot_of_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal go = 1 < 2\nplot(go)"))


def test_order_when_bool_passes_and_side_qty_are_not_type_checked() -> None:
    """side/qty_expr는 §3.3에 별도 프로덕션이 없는 opaque Expr이라 검사
    대상이 아니다 — `buy`를 어디에도 선언하지 않아도 통과해야 한다."""
    check_program(parse("order(buy, 1) when 1 < 2"))


def test_order_when_non_bool_is_rejected() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("order(buy, 1) when 1 + 2"))


# ---- 통합: DSL-3 round-trip 테스트와 같은 형태의 스크립트 전체가 통과 ----


def test_full_program_type_checks_end_to_end() -> None:
    source = (
        "input length: int = 14\n"
        "input close: series<float> = 0\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "let prev = close[1]\n"
        "signal go_long = rsi_val < 30 and close > prev\n"
        "plot(rsi_val)\n"
        "order(buy, 1) when go_long"
    )
    env = check_program(parse(source))
    assert env == {
        "length": "int",
        "close": "series<float>",
        "rsi_val": "series<float>",
        "prev": "float",
        "go_long": "series<bool>",
    }
