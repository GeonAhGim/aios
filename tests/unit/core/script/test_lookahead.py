"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-5 —
`analysis/lookahead.py` 테스트.

DoD: (1) 음수 인덱스·변수 인덱스·security()류 호출 각각 negative test,
(2) 모든 위반이 `SCRIPT_LOOKAHEAD` 하나이고 위치가 DSL-2 토큰 위치와
일치, (3) 과거 오프셋·상수 인덱스만 쓰는 정상 스크립트는 오탐 0.

DEEPEN(task-2913, task-2727 감사 후속): D2 하한 보강 — 실패 주입 1,
수치 성능 단언 1(정적 검출 지연, ADR-2026-09-09-C), 게이트 적색 재현 1
(security() 네임스페이스 우회 회귀 가드). negative는 이미 6건으로 충분해
추가하지 않는다.
"""

from __future__ import annotations

import time
from collections.abc import Sequence

import pytest

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_lookahead, check_source
from src.core.script.grammar.lexer import Token, TokenKind, tokenize

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
    ident_tok = next(t for t in tokens if t.kind is TokenKind.IDENT and t.value == "i")

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
    security_tok = next(t for t in tokens if t.kind is TokenKind.IDENT and t.value == "security")

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


# ---- 실패 주입: 업스트림(DSL-2) 토큰열이 잘려도 fail-closed ----


def test_truncated_token_stream_from_upstream_failure_fails_closed() -> None:
    """실패 주입: 렉서가 버그·크래시로 EOF 없이 끊긴 토큰열을 넘기는 최악의
    경우를 흉내낸다("[" 하나만 있고 그 뒤가 아예 없음 — `tokenize()`가 절대
    만들지 않는 형태를 손으로 조립). `_at`는 범위를 벗어나면 마지막 토큰을
    재사용하므로 판정에 필요한 `NUMBER "]"`를 얻지 못하고, 그 경우 "판정
    불가"는 통과가 아니라 거부다(모듈 docstring fail-closed decision). 이
    테스트는 그 계약이 크래시(IndexError)나 무단 통과가 아니라 반드시
    `ScriptLookaheadError`로 귀결됨을 못박는다."""
    truncated: Sequence[Token] = [Token(TokenKind.DELIM, "[", "LBRACKET", 1, 5)]

    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_lookahead(truncated)

    assert excinfo.value.code == "SCRIPT_LOOKAHEAD"


# ---- 성능 단언: 정적 검출 지연(ADR-2026-09-09-C) ----


@pytest.mark.perf
def test_static_detection_latency_p95_within_compile_budget_slice() -> None:
    """수치 성능 단언: ADR-2026-09-09-C 축별 성능 예산 "DSL 컴파일 300ms"
    중 lookahead 정적 검출 단계 몫을 50ms로 상한 잡는다(나머지는 렉스/파스/
    타입체크 몫 — 이 리프가 전체 예산을 다 써버리면 안 된다). 500문 스크립트
    30회 반복의 p95로 1회성 지터를 흡수한다."""
    source = "\n".join(f"let v{i} = close[{i % 5}] + close[0]" for i in range(500))
    tokens = tokenize(source)
    budget_sec = 0.05

    samples: list[float] = []
    for _ in range(30):
        start = time.perf_counter()
        check_lookahead(tokens)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[min(int(len(samples) * 0.95), len(samples) - 1)]

    print(f"[DSL-5 lookahead] 500-stmt p95={p95 * 1e3:.3f}ms budget<{budget_sec * 1e3:.0f}ms")
    assert p95 < budget_sec


# ---- 게이트 적색 재현: security() 네임스페이스 우회 회귀 가드 ----


def test_security_namespace_bypass_regression_guard() -> None:
    """게이트 적색 재현: `_check_future_call`이 "(" 뒤만 보고 "." 분기(ns
    자리에 온 security류 — `security.get_bar(...)`처럼 security류를
    네임스페이스로 써서 실제 위험한 호출은 뒤의 `get_bar`인 척하는 우회)를
    빠뜨리는 회귀가 나면 이 입력은 미탐(=게이트가 적색이어야 할 자리에서
    녹색)한다. 그 결여된 버전을 아래에서 그대로 재현해 미탐이 실제로
    재현됨을 먼저 보이고("(" 만 보면 못 잡음), 이어서 현재 구현이 "." 분기
    덕에 동일 입력을 정확히 거부함을 확인한다(회귀 가드)."""
    source = "let x = security.get_bar(a, b)"
    tokens = tokenize(source)

    def _naive_check_ident_paren_only(tokens: Sequence[Token]) -> bool:
        """수정 전(가상) 구현: ns 자리 우회를 못 잡는 "(" 전용 버전."""
        future_names = {"security", "request_security"}
        for i, tok in enumerate(tokens):
            if tok.kind is TokenKind.IDENT and tok.value in future_names:
                nxt = tokens[i + 1] if i + 1 < len(tokens) else tokens[-1]
                if nxt.kind is TokenKind.DELIM and nxt.value == "(":
                    return True
        return False

    # 게이트 적색 재현: "." 분기 없이는 네임스페이스 우회를 놓친다(미탐).
    assert _naive_check_ident_paren_only(tokens) is False

    # 회귀 가드: 현재 구현은 "." 분기 덕에 동일 입력을 정확히 거부한다.
    with pytest.raises(ScriptLookaheadError) as excinfo:
        check_lookahead(tokens)
    assert excinfo.value.code == "SCRIPT_LOOKAHEAD"
