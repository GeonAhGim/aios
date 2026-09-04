"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-3 —
`grammar/parser.py` 테스트.

§3.3 문법표의 모든 프로덕션에 대해 최소 1개의 파싱 성공 케이스를 확인하고,
우선순위(or<and<not<cmp<arith<term<unary<postfix) 결합, negative(음수·
변수 인덱스, 미지 네임스페이스, 미종결 괄호, 문법표 밖 키워드), 그리고
파서 산출물이 DSL-1 직렬화 왕복에 대해 항등임을 확인한다.
"""
from __future__ import annotations

import pytest

from src.core.script.grammar.ast import (
    BinaryExpr,
    CallExpr,
    Identifier,
    InputDecl,
    LetDecl,
    NotExpr,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    PostfixExpr,
    Program,
    SignalDecl,
    TypeNode,
    UnaryExpr,
    program_from_dict,
    to_dict,
)
from src.core.script.grammar.lexer import ScriptSyntaxError
from src.core.script.grammar.parser import parse

# ---- §3.3 decl 5종: 파싱 성공 ----


def test_input_decl_scalar_type() -> None:
    program = parse("input length: int = 14")
    assert program.decls == (InputDecl(name="length", type=TypeNode(name="int"), value=14),)


def test_input_decl_series_type() -> None:
    program = parse("input src: series<float> = 0")
    decl = program.decls[0]
    assert isinstance(decl, InputDecl)
    assert decl.type.name == "series<float>"


def test_let_decl() -> None:
    program = parse("let rsi_val = ta.rsi(close, 14)")
    assert program.decls == (
        LetDecl(
            name="rsi_val",
            expr=CallExpr(
                ns="ta", ident="rsi", args=(Identifier(name="close"), NumberLiteral(value=14))
            ),
        ),
    )


def test_plot_decl_without_style() -> None:
    program = parse("plot(score)")
    assert program.decls == (PlotDecl(expr=Identifier(name="score"), style=None),)


def test_plot_decl_with_style() -> None:
    program = parse("plot(score, 1)")
    assert program.decls == (PlotDecl(expr=Identifier(name="score"), style=NumberLiteral(value=1)),)


def test_signal_decl() -> None:
    program = parse("signal go_long = rsi_val < 30")
    decl = program.decls[0]
    assert isinstance(decl, SignalDecl)
    assert decl.name == "go_long"
    assert decl.expr == BinaryExpr(
        op="<", left=Identifier(name="rsi_val"), right=NumberLiteral(value=30)
    )


def test_order_decl_without_opts() -> None:
    program = parse('order(buy, 1) when close > 0')
    assert program.decls == (
        OrderDecl(
            side=Identifier(name="buy"),
            qty_expr=NumberLiteral(value=1),
            opts=None,
            when=BinaryExpr(op=">", left=Identifier(name="close"), right=NumberLiteral(value=0)),
        ),
    )


def test_order_decl_with_opts() -> None:
    program = parse("order(buy, 1, 2) when close > 0")
    decl = program.decls[0]
    assert isinstance(decl, OrderDecl)
    assert decl.opts == NumberLiteral(value=2)


def test_program_is_decl_star_multiple_decls() -> None:
    program = parse("let a = 1\nlet b = 2\nlet c = 3")
    assert [d.name for d in program.decls if isinstance(d, LetDecl)] == ["a", "b", "c"]


def test_empty_program_has_no_decls() -> None:
    assert parse("").decls == ()


# ---- expr 계층 프로덕션: 개별 성공 케이스 ----


def test_or_expr() -> None:
    decl = parse("let x = a or b").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op="or", left=Identifier(name="a"), right=Identifier(name="b"))


def test_and_expr() -> None:
    decl = parse("let x = a and b").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op="and", left=Identifier(name="a"), right=Identifier(name="b"))


def test_not_expr() -> None:
    decl = parse("let x = not a").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == NotExpr(operand=Identifier(name="a"))


@pytest.mark.parametrize("op", ["<", "<=", "==", ">=", ">", "crosses_above", "crosses_below"])
def test_cmp_all_operators(op: str) -> None:
    decl = parse(f"let x = a {op} b").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op=op, left=Identifier(name="a"), right=Identifier(name="b"))  # type: ignore[arg-type]


@pytest.mark.parametrize("op", ["+", "-"])
def test_arith_operators(op: str) -> None:
    decl = parse(f"let x = a {op} b").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op=op, left=Identifier(name="a"), right=Identifier(name="b"))  # type: ignore[arg-type]


@pytest.mark.parametrize("op", ["*", "/"])
def test_term_operators(op: str) -> None:
    decl = parse(f"let x = a {op} b").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op=op, left=Identifier(name="a"), right=Identifier(name="b"))  # type: ignore[arg-type]


def test_unary_minus() -> None:
    decl = parse("let x = -a").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == UnaryExpr(op="-", operand=Identifier(name="a"))


def test_unary_minus_is_right_associative_for_repeats() -> None:
    decl = parse("let x = - -a").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == UnaryExpr(op="-", operand=UnaryExpr(op="-", operand=Identifier(name="a")))


def test_postfix_constant_index() -> None:
    decl = parse("let x = close[1]").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == PostfixExpr(base=Identifier(name="close"), index=1)


def test_postfix_zero_index() -> None:
    decl = parse("let x = close[0]").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == PostfixExpr(base=Identifier(name="close"), index=0)


def test_primary_number() -> None:
    decl = parse("let x = 1.5").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == NumberLiteral(value=1.5)


def test_primary_ident() -> None:
    decl = parse("let x = close").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == Identifier(name="close")


def test_primary_parenthesized_expr() -> None:
    decl = parse("let x = (a + b)").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(op="+", left=Identifier(name="a"), right=Identifier(name="b"))


@pytest.mark.parametrize("ns", ["ta", "math", "series"])
def test_call_all_namespaces(ns: str) -> None:
    decl = parse(f"let x = {ns}.f(a)").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == CallExpr(ns=ns, ident="f", args=(Identifier(name="a"),))


def test_call_no_args() -> None:
    decl = parse("let x = math.pi()").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == CallExpr(ns="math", ident="pi", args=())


def test_call_multiple_args() -> None:
    decl = parse("let x = ta.sma(close, 20)").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == CallExpr(
        ns="ta", ident="sma", args=(Identifier(name="close"), NumberLiteral(value=20))
    )


@pytest.mark.parametrize(
    "type_src,expected",
    [
        ("int", "int"),
        ("float", "float"),
        ("bool", "bool"),
        ("series<float>", "series<float>"),
        ("series<bool>", "series<bool>"),
    ],
)
def test_type_all_variants(type_src: str, expected: str) -> None:
    program = parse(f"input x: {type_src} = 0")
    decl = program.decls[0]
    assert isinstance(decl, InputDecl)
    assert decl.type.name == expected


# ---- 우선순위 결합: or < and < not < cmp < arith < term < unary < postfix ----


def test_operator_precedence_full_chain() -> None:
    decl = parse("let x = a or b and not c < d + e * -f[0]").decls[0]
    assert isinstance(decl, LetDecl)

    postfix = PostfixExpr(base=Identifier(name="f"), index=0)
    unary = UnaryExpr(op="-", operand=postfix)
    term = BinaryExpr(op="*", left=Identifier(name="e"), right=unary)
    arith = BinaryExpr(op="+", left=Identifier(name="d"), right=term)
    cmp = BinaryExpr(op="<", left=Identifier(name="c"), right=arith)
    not_expr = NotExpr(operand=cmp)
    and_expr = BinaryExpr(op="and", left=Identifier(name="b"), right=not_expr)
    or_expr = BinaryExpr(op="or", left=Identifier(name="a"), right=and_expr)

    assert decl.expr == or_expr


def test_arith_and_term_are_left_associative() -> None:
    decl = parse("let x = a - b - c").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(
        op="-",
        left=BinaryExpr(op="-", left=Identifier(name="a"), right=Identifier(name="b")),
        right=Identifier(name="c"),
    )


def test_term_binds_tighter_than_arith() -> None:
    decl = parse("let x = a + b * c").decls[0]
    assert isinstance(decl, LetDecl)
    assert decl.expr == BinaryExpr(
        op="+",
        left=Identifier(name="a"),
        right=BinaryExpr(op="*", left=Identifier(name="b"), right=Identifier(name="c")),
    )


# ---- negative: SCRIPT_SYNTAX + 정확한 (line, col) ----


def test_negative_index_is_script_syntax_at_minus_position() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = a[-1]")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 11  # '-' 위치


def test_variable_index_is_script_syntax_at_ident_position() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = a[i]")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 11  # 'i' 위치


def test_unknown_namespace_is_script_syntax_at_namespace_position() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = foo.bar()")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 9  # 'foo' 위치


def test_unterminated_paren_is_script_syntax_at_eof() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = (1 + 2")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 15  # 소스 끝(EOF)


def test_unterminated_call_args_is_script_syntax() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = ta.rsi(close, 14")
    assert excinfo.value.code == "SCRIPT_SYNTAX"


@pytest.mark.parametrize("keyword", ["for", "while", "security"])
def test_grammar_keywords_outside_table_are_script_syntax(keyword: str) -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse(f"{keyword}(a)")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 1  # 소스 맨 앞의 미지 키워드 위치


def test_decl_starting_with_unknown_token_reports_its_own_position() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        parse("let x = 1\nwhile(true)")
    err = excinfo.value
    assert err.line == 2
    assert err.col == 1


def test_missing_when_after_order_is_script_syntax() -> None:
    with pytest.raises(ScriptSyntaxError):
        parse("order(buy, 1) close > 0")


def test_series_inner_type_must_be_float_or_bool() -> None:
    with pytest.raises(ScriptSyntaxError):
        parse("input x: series<int> = 0")


# ---- 파서는 DSL-2 토큰만 입력받고 DSL-1 노드만 출력한다: 직렬화 왕복 항등 ----


def test_parser_output_round_trips_through_dsl1_serialization() -> None:
    source = (
        "input length: int = 14\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "let prev = close[1]\n"
        "signal go_long = rsi_val < 30 and close > prev\n"
        "plot(rsi_val)\n"
        "order(buy, 1) when go_long"
    )
    program = parse(source)

    restored = program_from_dict(to_dict(program))

    assert restored == program
    assert isinstance(program, Program)
    assert to_dict(restored) == to_dict(program)
