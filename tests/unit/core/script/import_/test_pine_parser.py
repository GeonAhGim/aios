"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-14 —
`import_/pine/parser.py` 테스트.

지원 문법(허용 목록) 전부에 최소 1개의 성공 케이스, 그리고 미지원 구문
전부에 위치(line, col)를 담은 거부를 확인한다. 스크립트는 전부 직접 작성한
최소 예제다(task-2307 decision: TradingView 원문 반입 금지).
"""
from __future__ import annotations

import pytest

from src.core.script.import_.pine.lexer import PineSyntaxError
from src.core.script.import_.pine.parser import (
    AssignStmt,
    BinaryExpr,
    BoolLiteral,
    CallArg,
    CallExpr,
    ExprStmt,
    Identifier,
    NotExpr,
    NumberLiteral,
    PostfixExpr,
    StringLiteral,
    UnaryExpr,
    parse,
)

# ---- 지원 문법: 성공 케이스 ----


def test_assignment_statement() -> None:
    program = parse("len = 14")
    assert program.statements == (AssignStmt(name="len", expr=NumberLiteral(value=14)),)


def test_number_string_bool_literals() -> None:
    program = parse('plot(1)\nplot(1.5)\nplot("x")\nplot(true)\nplot(false)')
    calls: list[CallExpr] = []
    for stmt in program.statements:
        assert isinstance(stmt, ExprStmt)
        assert isinstance(stmt.expr, CallExpr)
        calls.append(stmt.expr)
    assert calls[0].args[0].value == NumberLiteral(value=1)
    assert calls[1].args[0].value == NumberLiteral(value=1.5)
    assert calls[2].args[0].value == StringLiteral(value="x")
    assert calls[3].args[0].value == BoolLiteral(value=True)
    assert calls[4].args[0].value == BoolLiteral(value=False)


def test_plain_identifier_reference() -> None:
    program = parse("plot(close)")
    stmt = program.statements[0]
    assert isinstance(stmt, ExprStmt)
    assert isinstance(stmt.expr, CallExpr)
    assert stmt.expr.args[0].value == Identifier(name="close")


def test_series_arithmetic_comparison_and_logical_precedence() -> None:
    # or < and < not < cmp < arith < term, 전부 좌결합
    program = parse("x = a + b * c > d and not e or f")
    expr = program.statements[0].expr
    assert isinstance(expr, BinaryExpr) and expr.op == "or"
    left = expr.left
    assert isinstance(left, BinaryExpr) and left.op == "and"
    cmp_expr = left.left
    assert isinstance(cmp_expr, BinaryExpr) and cmp_expr.op == ">"
    arith = cmp_expr.left
    assert isinstance(arith, BinaryExpr) and arith.op == "+"
    term = arith.right
    assert isinstance(term, BinaryExpr) and term.op == "*"
    assert isinstance(left.right, NotExpr)


def test_unary_minus_and_percent_and_neq() -> None:
    program = parse("x = -a % b != c")
    expr = program.statements[0].expr
    assert isinstance(expr, BinaryExpr) and expr.op == "!="
    mod = expr.left
    assert isinstance(mod, BinaryExpr) and mod.op == "%"
    assert isinstance(mod.left, UnaryExpr) and mod.left.op == "-"


def test_past_reference_postfix() -> None:
    program = parse("x = close[1]")
    expr = program.statements[0].expr
    assert expr == PostfixExpr(base=Identifier(name="close"), index=1)


def test_ta_namespace_call_with_positional_args() -> None:
    program = parse("sma = ta.sma(close, 14)")
    expr = program.statements[0].expr
    assert expr == CallExpr(
        ns="ta",
        ident="sma",
        args=(
            CallArg(name=None, value=Identifier(name="close")),
            CallArg(name=None, value=NumberLiteral(value=14)),
        ),
    )


def test_input_namespace_call_with_named_arg() -> None:
    program = parse('len = input.int(14, title="Length")')
    expr = program.statements[0].expr
    assert isinstance(expr, CallExpr)
    assert expr.ns == "input"
    assert expr.ident == "int"
    assert expr.args[1] == CallArg(name="title", value=StringLiteral(value="Length"))


def test_plot_bare_call() -> None:
    program = parse("plot(close)")
    assert isinstance(program.statements[0], ExprStmt)


def test_strategy_entry_and_exit_calls() -> None:
    program = parse('strategy.entry("Long", 1)\nstrategy.exit("Exit", "Long")')
    entry = program.statements[0].expr
    exit_ = program.statements[1].expr
    assert isinstance(entry, CallExpr) and entry.ns == "strategy" and entry.ident == "entry"
    assert isinstance(exit_, CallExpr) and exit_.ns == "strategy" and exit_.ident == "exit"


# ---- 미지원 구문: 위치(line, col) 포함 거부 ----


def test_request_security_namespace_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse('x = request.security(syminfo.tickerid, "D", close)')
    assert exc.value.line == 1
    assert "request" in exc.value.message


def test_array_namespace_rejected() -> None:
    with pytest.raises(PineSyntaxError):
        parse("a = array.new_float(0)")


def test_bare_call_other_than_plot_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse('alert("boom")')
    assert "alert" in exc.value.message


def test_indicator_declarator_call_rejected_out_of_scope() -> None:
    with pytest.raises(PineSyntaxError):
        parse('indicator("My Script")')


def test_strategy_close_not_entry_exit_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse('strategy.close("Long")')
    assert "strategy.close" in exc.value.message


def test_strategy_value_reference_without_call_rejected() -> None:
    with pytest.raises(PineSyntaxError):
        parse("dir = strategy.long")


def test_user_function_definition_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse("f(x) => x * 2")
    assert exc.value.line == 1


def test_arrow_after_allowed_call_rejected() -> None:
    """`plot(x) => x`처럼 허용된 이름 뒤에 화살표가 와도 거부돼야 한다 —
    호출 자체는 문법적으로 유효해 바깥 호출 허용목록 검사를 통과하므로
    문장 종결부의 ARROW 검사가 실제로 작동하는지 확인한다."""
    with pytest.raises(PineSyntaxError) as exc:
        parse("plot(x) => x")
    assert exc.value.line == 1


def test_if_block_rejected_with_position() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse("if close > open\n    x = 1")
    assert exc.value.line == 1
    assert exc.value.col == 1


def test_var_declaration_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse("var x = 0")
    assert "var" in exc.value.message


def test_reassignment_operator_rejected() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        parse("x = 1\nx := 2")
    assert exc.value.line == 2


def test_negative_postfix_index_rejected() -> None:
    with pytest.raises(PineSyntaxError):
        parse("x = close[-1]")


def test_variable_postfix_index_rejected() -> None:
    with pytest.raises(PineSyntaxError):
        parse("n = 1\nx = close[n]")


def test_unclosed_paren_rejected() -> None:
    with pytest.raises(PineSyntaxError):
        parse("x = ta.sma(close, 14")


def test_full_minimal_script_parses_end_to_end() -> None:
    source = "\n".join(
        [
            "len = input.int(14, title=\"Length\")",
            "sma = ta.sma(close, len)",
            "plot(sma)",
            "strategy.entry(\"Long\", 1)",
        ]
    )
    program = parse(source)
    assert len(program.statements) == 4
    assert isinstance(program.statements[0], AssignStmt)
    assert isinstance(program.statements[3], ExprStmt)
