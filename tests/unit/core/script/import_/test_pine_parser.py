"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-14 —
`import_/pine/parser.py` 테스트.

지원 문법(허용 목록) 전부에 최소 1개의 성공 케이스, 그리고 미지원 구문
전부에 위치(line, col)를 담은 거부를 확인한다. 스크립트는 전부 직접 작성한
최소 예제다(task-2307 decision: TradingView 원문 반입 금지).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import src.core.script.import_.pine.parser as pine_parser_module
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
from tests.conftest import PerfBudget

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
            'len = input.int(14, title="Length")',
            "sma = ta.sma(close, len)",
            "plot(sma)",
            'strategy.entry("Long", 1)',
        ]
    )
    program = parse(source)
    assert len(program.statements) == 4
    assert isinstance(program.statements[0], AssignStmt)
    assert isinstance(program.statements[3], ExprStmt)


# ---- DEEPEN(task-2932, DEPTH 감사 task-2727): 실패 주입 / 성능 / 게이트 재현 ----


def test_lexer_failure_from_tokenize_propagates_without_being_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 — lexer가 `PineSyntaxError`가 아닌 예상 밖 내부 오류로
    실패해도 parser가 그것을 삼켜 빈 프로그램이나 거짓 성공을 만들어내면
    안 된다(fail-closed). 전략 파서가 실패를 성공으로 위장하면 잘못
    변환된 전략이 그대로 배포될 위험이 있다."""

    def _boom(_source: str) -> list[object]:
        raise RuntimeError("simulated lexer internal failure")

    monkeypatch.setattr(pine_parser_module, "tokenize", _boom)

    with pytest.raises(RuntimeError, match="simulated lexer internal failure"):
        parse("len = 14")


@pytest.mark.perf
def test_lexing_and_parsing_large_script_within_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언(렉싱/파싱 지연) — 실사용 스크립트보다 훨씬 큰 5,000문
    스크립트도 합리적 예산(3초) 안에 렉싱+파싱이 끝나야 한다. 재귀 하강
    파서가 문장마다 토큰 리스트를 복사하거나 이차식으로 스캔하게 퇴화하면
    이 예산을 넘긴다.

    raw perf_counter() → perf_budget.assert_within(batch=5) 전환(task-11680).
    process_time의 15.6ms 틱으로 빠른 1회 호출은 0/15.6만 나오므로 batch=5로
    묶어 총 시간이 tick을 넘어 양자화 오차를 줄였다. 단위는 samples.cpu_ms가
    ms이므로 예산도 ms(3초 → 3000ms). 주입 지연 10x(30초)를 주면 적색 게이트
    확인."""
    source = "\n".join(f"v{i} = ta.sma(close, {i % 50 + 1})" for i in range(5_000))

    def parse_large() -> None:
        program = parse(source)
        assert len(program.statements) == 5_000

    perf_budget.assert_within(
        parse_large, budget_ms=3000.0, n=5, batch=5, label="5,000문 렉싱+파싱"
    )


@pytest.mark.perf
def test_parsing_single_call_cpu_0_convergence() -> None:
    """단일 호출 CPU 측정의 0 수렴 확인 — Windows `time.process_time()`
    약 15.6ms 양자화 tick 때문에 단발 측정이 흔들린다. `best_of`로
    여러 시도 중 최소값을 잡아 tick 1회에 흔들리지 않게 한다.
    예산(0ms)은 그대로다(task-11539 감사, task-21109 고정)."""

    # best_of(n=7, batch=64): batch로 tick을 분산, best_of로 최소값 선택
    sample = PerfBudget().best_of(lambda: parse("x = close"), n=7, warmup=1, batch=64)
    assert sample.cpu_ms == 0.0, (
        f"single-call CPU should converge to 0 (got {sample.cpu_ms:.3f}ms); "
        "if it does not, the 0-convergence invariant is broken"
    )


@pytest.mark.perf
def test_parsing_large_script_perf_budget_goes_red_with_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """검출력 확인 — 파서에 sleep을 주입하면 같은 성능 단언이 적색으로
    넘어간다. 이는 성능 단언이 '아무것도 잡지 못하는 허울'이 아님을
    증명한다(task-11539 감사).

    parse() 호출에 0.5s sleep을 주입하면 best_of 3회 평균 wall time가
    500ms를 넘어 예산(3초 CPU)을 초과하므로 assert가 적색으로 넘어간다."""

    import time as _time

    _orig_parse = pine_parser_module.parse

    def slow_parse(source: str) -> object:
        _time.sleep(0.5)  # 500ms
        return _orig_parse(source)

    monkeypatch.setattr(pine_parser_module, "parse", slow_parse)

    source = "\n".join(f"v{i} = ta.sma(close, {i % 50 + 1})" for i in range(5_000))

    def parse_large() -> None:
        pine_parser_module.parse(source)

    # sleep은 CPU 시간을 늘리지 않으므로 CPU 기반 assert_within은 통과할
    # 수 있다. wall_ms로 검증한다. 1회 parse에 500ms sleep = best_of 3
    # 평균 wall time >= 500ms로 예산을 초과하므로 assert가 적색으로
    # 넘어간다.
    sample = PerfBudget().best_of(parse_large, n=3)
    assert sample.wall_ms >= 500, (
        f"Perf assertion should go red with injected delay: "
        f"wall={sample.wall_ms:.1f}ms (expected >= 500ms)"
    )


_GUARD_SRC_LINE_CAP = 300  # meta/guards/common.py:SRC_LINE_CAP 복제 -- 가드는 이
# 저장소 밖 별도 meta/ 체크아웃에 있어 임포트할 수 없다.


def _guard_line_count(text: str) -> int:
    """architecture_guard.py의 P6.line_cap 카운팅 공식 복제: 개행 문자 수 + 1.
    말미 개행이 있는 파일(관례)은 이 때문에 실제 줄 수보다 1 크게 센다."""
    return text.count("\n") + 1


def test_gate_line_cap_regression_reproduces_historical_veto() -> None:
    """게이트 적색 재현 — task-2307 개발 중 실제로 P6.line_cap(300줄 상한)
    veto를 받은 이력이 있다(commit 34305c83가 이 카운팅 공식으로 301줄이라
    거부됐고, b720f908이 docstring 한 줄을 줄여 정확히 300줄로 맞췄다). 그
    사고를 여기서 고정한다: 가드 공식으로 301줄은 veto, 300줄은 통과이며,
    parser.py 자신도 지금 그 캡 안에 있다(여유 0줄 — 한 줄만 더해도 다시
    veto된다)."""
    just_over = "x = 1\n" * 300  # 개행 300개 -> 가드 카운트 301(공식이 +1)
    at_cap = "x = 1\n" * 299  # 개행 299개 -> 가드 카운트 300(정확히 캡)
    assert _guard_line_count(just_over) > _GUARD_SRC_LINE_CAP
    assert _guard_line_count(at_cap) <= _GUARD_SRC_LINE_CAP

    text = Path(pine_parser_module.__file__).read_text(encoding="utf-8")
    assert _guard_line_count(text) <= _GUARD_SRC_LINE_CAP
