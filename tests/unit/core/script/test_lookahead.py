"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-5 —
`analysis/lookahead.py` 테스트.

DoD: (1) 음수 인덱스·변수 인덱스·security()류 호출 각각 negative test,
(2) 모든 위반이 `SCRIPT_LOOKAHEAD` 하나이고 위치가 DSL-2 토큰 위치와
일치, (3) 과거 오프셋·상수 인덱스만 쓰는 정상 스크립트는 오탐 0.
"""
from __future__ import annotations

import pytest

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_lookahead, check_source
from src.core.script.grammar.lexer import TokenKind, tokenize

# ---- negative: (a) 음수 인덱스 ----


def test_negative_index_is_script_lookahead_at_minus_position() -> None:
    source = "let x = a[-1]"
    tokens = tokenize(source)
    minus_tok = next(t for t in tokens if t.kind is TokenKind.OP and t.value == "-")

    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(source)

    err = excinfo.value
    assert err.code == "SCRIPT_LOOKAHEAD"
    assert (err.line, err.col) == (minus_tok.line, minus_tok.col)


def test_negative_index_in_nested_expr_is_rejected() -> None:
    source = "signal go = close[0] > close[-2]"
    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(source)
    assert excinfo.value.code == "SCRIPT_LOOKAHEAD"


# ---- negative: (b) 변수 인덱스(상수로 접히지 않음 — fail-closed) ----


def test_variable_index_is_script_lookahead_at_ident_position() -> None:
    source = "let x = a[i]"
    tokens = tokenize(source)
    ident_tok = next(
        t for t in tokens if t.kind is TokenKind.IDENT and t.value == "i"
    )

    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(source)

    err = excinfo.value
    assert err.code == "SCRIPT_LOOKAHEAD"
    assert (err.line, err.col) == (ident_tok.line, ident_tok.col)


@pytest.mark.parametrize("expr", ["a[1.5]", "a[n+1]", "a[(1)]"])
def test_index_that_does_not_fold_to_a_plain_constant_is_rejected(expr: str) -> None:
    """정적으로 "미래 아님"을 증명할 수 없으면 통과가 아니라 거부(fail-closed)."""
    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(f"let x = {expr}")
    assert excinfo.value.code == "SCRIPT_LOOKAHEAD"


# ---- negative: (c) security()류 미래 데이터 접근 함수 호출 ----


@pytest.mark.parametrize(
    "source",
    [
        "let x = security(a, b)",
        "let x = ta.security(a)",
        "let x = security.request(a)",
    ],
)
def test_future_function_call_is_script_lookahead_at_ident_position(source: str) -> None:
    tokens = tokenize(source)
    security_tok = next(
        t for t in tokens if t.kind is TokenKind.IDENT and t.value == "security"
    )

    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(source)

    err = excinfo.value
    assert err.code == "SCRIPT_LOOKAHEAD"
    assert (err.line, err.col) == (security_tok.line, security_tok.col)


def test_identifier_named_security_without_call_is_not_flagged() -> None:
    """오탐 방지: `security`가 호출/네임스페이스 위치가 아니면 허용."""
    check_source("let security = 1")


# ---- 세 위반 모두 동일한 taxonomy 코드 ----


@pytest.mark.parametrize(
    "source",
    ["let x = a[-1]", "let x = a[i]", "let x = security(a)"],
)
def test_all_violations_share_single_error_code(source: str) -> None:
    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_source(source)
    assert excinfo.value.code == "SCRIPT_LOOKAHEAD"


# ---- positive: 과거 오프셋·상수 인덱스만 쓰는 정상 스크립트는 오탐 0 ----


def test_clean_script_with_past_offsets_and_constant_indices_passes() -> None:
    source = (
        "input length: int = 14\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "let prev = close[1]\n"
        "let prev_prev = close[2]\n"
        "let base = close[0]\n"
        "let smoothed = math.abs(prev - prev_prev)\n"
        "signal go_long = rsi_val < 30 and close > prev\n"
        "plot(rsi_val)\n"
        "order(buy, 1) when go_long"
    )
    check_source(source)  # 예외 없이 통과해야 한다
    check_lookahead(tokenize(source))  # 토큰 리스트를 직접 넘겨도 동일


def test_empty_program_passes() -> None:
    check_source("")


# ---- 순수 정적 분석: 스크립트를 실행하지 않는다 ----


def test_analysis_does_not_require_program_ast_or_execution() -> None:
    """`check_lookahead`는 DSL-2 토큰만으로 완결된다 — 파싱·실행이 필요 없다."""
    tokens = tokenize("let x = close[0]")
    check_lookahead(tokens)  # AST를 만들지 않고도 검사가 끝난다
