"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-15 —
`import_/pine/transpile.py` 테스트.

스크립트는 전부 직접 작성한 최소 예제다(task-2307 decision과 동일하게
TradingView 원문 반입 금지 — 라이선스 문제 없이 흔한 Pine 관용구를 재현만
한다). 두 갈래로 나눈다:

1. 결정 단위 테스트: 모듈 docstring이 명시한 변환 규칙(그리고 그 반대인
   거부 규칙) 하나하나에 대응하는 최소 케이스.
2. `_CORPUS`(30개, DoD "공개 예제 30개"): 성공 20개 + 거부 10개를 한 번에
   돌려 컴파일 통과율을 집계·보고한다(DoD "변환 후 컴파일 통과율 보고").
   거부 10개는 실패가 나는 계층(Pine 파서 DSL-14 vs 이 리프의 변환)과 이유를
   구분해 "의미 차이 명시" DoD를 정확한 예외 타입으로 고정한다.
"""
from __future__ import annotations

import pytest

from src.core.script.grammar.ast import (
    BinaryExpr,
    CallExpr,
    Identifier,
    InputDecl,
    LetDecl,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    PostfixExpr,
    TypeNode,
)
from src.core.script.import_.pine.lexer import PineSyntaxError
from src.core.script.import_.pine.transpile import (
    PineTranspileError,
    transpile_and_verify,
    transpile_source,
)
from src.core.script.ir.ops import from_bytes, verify_stack

# ---- 결정 단위: 변환되는 것 ----


def test_assign_becomes_let_decl() -> None:
    program = transpile_source("x = 1 + 2")
    expected_expr = BinaryExpr(op="+", left=NumberLiteral(value=1), right=NumberLiteral(value=2))
    assert program.decls == (LetDecl(name="x", expr=expected_expr),)


def test_input_int_becomes_input_decl() -> None:
    program = transpile_source('len = input.int(14, title="Length")')
    assert program.decls == (
        InputDecl(name="len", type=TypeNode(name="int"), value=14),
    )


def test_input_float_becomes_input_decl_with_float_value() -> None:
    program = transpile_source("mult = input.float(2, title=\"Mult\")")
    decl = program.decls[0]
    assert isinstance(decl, InputDecl)
    assert decl.type.name == "float"
    assert decl.value == 2.0


def test_input_bool_becomes_input_decl() -> None:
    program = transpile_source("useFilter = input.bool(true)")
    assert program.decls == (InputDecl(name="useFilter", type=TypeNode(name="bool"), value=True),)


def test_plot_becomes_plot_decl_and_drops_style_args() -> None:
    program = transpile_source('x = 1\nplot(x, title="X", color=1)')
    plot = program.decls[1]
    assert isinstance(plot, PlotDecl)
    assert plot.expr == Identifier(name="x")
    assert plot.style is None


def test_strategy_entry_becomes_order_decl_with_placeholder_when() -> None:
    program = transpile_source('strategy.entry("Long", 1, qty=2)')
    order = program.decls[0]
    assert isinstance(order, OrderDecl)
    assert order.side == NumberLiteral(value=1)
    assert order.qty_expr == NumberLiteral(value=2)
    assert order.opts is None
    expected_when = BinaryExpr(op="==", left=NumberLiteral(value=1), right=NumberLiteral(value=1))
    assert order.when == expected_when


def test_strategy_entry_without_qty_defaults_to_one() -> None:
    program = transpile_source('strategy.entry("Long", 1)')
    order = program.decls[0]
    assert isinstance(order, OrderDecl)
    assert order.qty_expr == NumberLiteral(value=1)


def test_ta_call_maps_positionally() -> None:
    program = transpile_source("sma = ta.sma(close, 20)")
    let_decl = program.decls[1]
    assert isinstance(let_decl, LetDecl)
    assert let_decl.expr == CallExpr(
        ns="ta", ident="sma", args=(Identifier(name="close"), NumberLiteral(value=20))
    )


def test_postfix_history_reference_preserved() -> None:
    program = transpile_source("prev = close[1]")
    let_decl = program.decls[1]
    assert isinstance(let_decl, LetDecl)
    assert let_decl.expr == PostfixExpr(base=Identifier(name="close"), index=1)


def test_ohlcv_identifier_auto_declared_once() -> None:
    program = transpile_source("a = close\nb = close + open")
    input_names = [d.name for d in program.decls if isinstance(d, InputDecl)]
    assert input_names == ["close", "open"]
    close_input = program.decls[0]
    assert isinstance(close_input, InputDecl)
    assert close_input.type.name == "series<float>"


def test_transpile_and_verify_round_trips_ir_bytes() -> None:
    result = transpile_and_verify("sma = ta.sma(close, 20)\nplot(sma)")
    verify_stack(from_bytes(result.ir_bytes))
    assert from_bytes(result.ir_bytes) == result.ir


# ---- 결정 단위: 변환되지 않는 것(전부 PineTranspileError, 사유별) ----


def test_strategy_exit_rejected() -> None:
    with pytest.raises(PineTranspileError, match="strategy.exit"):
        transpile_source('strategy.exit("Exit", "Long")')


def test_string_literal_expr_rejected() -> None:
    with pytest.raises(PineTranspileError, match="문자열 리터럴"):
        transpile_source('x = "hello"')


def test_bool_literal_outside_input_rejected() -> None:
    with pytest.raises(PineTranspileError, match="bool 리터럴"):
        transpile_source("x = true")


def test_modulo_operator_rejected() -> None:
    with pytest.raises(PineTranspileError, match="연산자"):
        transpile_source("x = close % 2")


def test_not_equal_operator_rejected() -> None:
    with pytest.raises(PineTranspileError, match="연산자"):
        transpile_source("x = close != open")


def test_keyword_arg_to_ta_call_rejected() -> None:
    with pytest.raises(PineTranspileError, match="키워드 인자"):
        transpile_source("x = ta.rsi(close, length=14)")


def test_nested_plot_call_rejected() -> None:
    with pytest.raises(PineTranspileError, match="네임스페이스"):
        transpile_source("x = plot(close) + 1")


def test_bare_call_statement_without_effect_rejected() -> None:
    with pytest.raises(PineTranspileError, match="부작용"):
        transpile_source("ta.sma(close, 10)")


def test_unsupported_input_kind_rejected() -> None:
    with pytest.raises(PineTranspileError, match="input.string"):
        transpile_source('sym = input.string("BTCUSD", title="Symbol")')


def test_input_default_must_be_literal() -> None:
    with pytest.raises(PineTranspileError, match="숫자 리터럴"):
        transpile_source("x = 1\nlen = input.int(x)")


# ---- 공개 예제 30개: 컴파일 통과율 ----

_OK = "ok"

_CORPUS: tuple[tuple[str, str, str | type[Exception]], ...] = (
    ("sma_plot", "sma = ta.sma(close, 20)\nplot(sma)", _OK),
    (
        "ema_with_input_length",
        'len = input.int(20, title="Length")\nema = ta.ema(close, len)\nplot(ema)',
        _OK,
    ),
    ("rsi_plot", "rsi = ta.rsi(close, 14)\nplot(rsi)", _OK),
    (
        "macd_like_two_lines",
        "fast = ta.ema(close, 12)\nslow = ta.ema(close, 26)\nmacd = fast - slow\n"
        "plot(macd)\nplot(fast)",
        _OK,
    ),
    (
        "bollinger_like_three_plots",
        "mid = ta.sma(close, 20)\nupper = mid + 2\nlower = mid - 2\n"
        "plot(mid)\nplot(upper)\nplot(lower)",
        _OK,
    ),
    (
        "crossover_bool_let_unused",
        "fast = ta.ema(close, 9)\nslow = ta.ema(close, 21)\nbull = fast > slow\nplot(fast)",
        _OK,
    ),
    ("typical_price", "tp = (high + low + close) / 3\nplot(tp)", _OK),
    ("history_reference", "prevClose = close[1]\nchg = close - prevClose\nplot(chg)", _OK),
    ("strategy_entry_basic", 'strategy.entry("Long", 1)', _OK),
    ("strategy_entry_with_qty", 'strategy.entry("Long", 1, qty=2)', _OK),
    (
        "input_bool_unused_filter",
        'useFilter = input.bool(true, title="Use Filter")\nplot(close)',
        _OK,
    ),
    (
        "input_float_multiplier",
        'mult = input.float(2.0, title="Multiplier")\nband = ta.sma(close, 20) * mult\nplot(band)',
        _OK,
    ),
    ("nested_calls_depth", "smoothed = ta.sma(ta.ema(close, 5), 5)\nplot(smoothed)", _OK),
    ("high_low_range", "diff = high - low\nplot(diff)", _OK),
    ("not_expr_unused", "isUp = not (close < open)\nplot(close)", _OK),
    (
        "and_or_condition_unused",
        "cond = (close > open) and (high > low)\nplot(close)",
        _OK,
    ),
    ("unary_negative", "neg = -close\nplot(neg)", _OK),
    ("half_spread", "spread = (high - low) / 2\nplot(spread)", _OK),
    ("two_strategy_entries", 'strategy.entry("Long", 1)\nstrategy.entry("Short", -1)', _OK),
    (
        "mini_ema_cross_strategy",
        'fastLen = input.int(9, title="Fast")\n'
        'slowLen = input.int(21, title="Slow")\n'
        "fast = ta.ema(close, fastLen)\n"
        "slow = ta.ema(close, slowLen)\n"
        "plot(fast)\n"
        "plot(slow)\n"
        'strategy.entry("Long", 1)',
        _OK,
    ),
    ("if_block_entry", 'if close > open\n    strategy.entry("Long", 1)', PineSyntaxError),
    (
        "request_security_multi_tf",
        'htf = request.security(syminfo.tickerid, "D", close)',
        PineSyntaxError,
    ),
    ("var_persistent", "var count = 0", PineSyntaxError),
    ("user_function_def", "f(x) => x * 2", PineSyntaxError),
    ("strategy_close_rejected_by_parser", 'strategy.close("Long")', PineSyntaxError),
    ("modulo_operator", "r = close % 2\nplot(r)", PineTranspileError),
    ("not_equal_operator", "neq = close != open\nplot(close)", PineTranspileError),
    ("strategy_exit", 'strategy.exit("Exit", "Long")', PineTranspileError),
    (
        "input_string_kind",
        'sym = input.string("BTCUSD", title="Symbol")\nplot(close)',
        PineTranspileError,
    ),
    ("dangling_call_statement", "ta.sma(close, 10)", PineTranspileError),
)


@pytest.mark.parametrize("case_id,source,expect", _CORPUS, ids=[c[0] for c in _CORPUS])
def test_public_corpus_case(case_id: str, source: str, expect: str | type[Exception]) -> None:
    if expect == _OK:
        transpile_and_verify(source)
        return
    assert isinstance(expect, type) and issubclass(expect, Exception)
    with pytest.raises(expect):
        transpile_and_verify(source)


def test_public_corpus_pass_rate_is_reported() -> None:
    """DoD "변환 후 컴파일 통과율 보고" — 30개 중 성공/실패 수를 집계해 고정한다.
    실패 10개는 전부 의도된 거부(파서 6 + 변환 4)로, 우연한 회귀가 아니다."""
    assert len(_CORPUS) == 30
    outcomes: list[bool] = []
    for _case_id, source, _expect in _CORPUS:
        try:
            transpile_and_verify(source)
            outcomes.append(True)
        except Exception:  # noqa: BLE001 — 통과율 집계이지 원인 분류가 아니다
            outcomes.append(False)
    pass_count = sum(outcomes)
    assert pass_count == 20
    assert len(outcomes) - pass_count == 10
