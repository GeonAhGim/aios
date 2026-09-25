"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-14 —
`import_/pine/lexer.py` 테스트.

직접 작성한 최소 Pine 조각만 사용한다(task-2307 decision: TradingView
코드·예제 원문을 저장소에 반입하지 않는다).
"""

from __future__ import annotations

import pytest

import src.core.script.import_.pine.lexer as pine_lexer
from src.core.script.import_.pine.lexer import KEYWORDS, PineSyntaxError, TokenKind, tokenize
from tests.conftest import PerfBudget


def _kinds(source: str) -> list[TokenKind]:
    return [tok.kind for tok in tokenize(source)]


def _values(source: str) -> list[str]:
    return [tok.value for tok in tokenize(source)]


def test_every_keyword_tokenizes_as_keyword() -> None:
    source = " ".join(sorted(KEYWORDS))
    tokens = tokenize(source)
    body = tokens[:-1]  # 마지막은 EOF
    assert len(body) == len(KEYWORDS)
    for tok in body:
        assert tok.kind is TokenKind.KEYWORD
        assert tok.value in KEYWORDS


def test_generic_identifiers_including_dotted_namespaces() -> None:
    tokens = tokenize("close ta input strategy my_var2")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.IDENT] * 5
    assert [t.value for t in body] == ["close", "ta", "input", "strategy", "my_var2"]


def test_integer_and_float_numbers() -> None:
    tokens = tokenize("14 0.5 100")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.NUMBER] * 3
    assert [t.value for t in body] == ["14", "0.5", "100"]


def test_all_comparison_arithmetic_and_logical_operators() -> None:
    tokens = tokenize("< <= == != >= > + - * / %")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.OP] * 11
    assert [t.value for t in body] == ["<", "<=", "==", "!=", ">=", ">", "+", "-", "*", "/", "%"]
    assert body[3].subtype == "NEQ"
    assert body[10].subtype == "PERCENT"


def test_reassign_and_arrow_tokenize_as_distinct_ops() -> None:
    tokens = tokenize(":= =>")
    body = tokens[:-1]
    assert [t.value for t in body] == [":=", "=>"]
    assert [t.subtype for t in body] == ["REASSIGN", "ARROW"]


def test_double_and_single_quoted_strings() -> None:
    tokens = tokenize("\"Length\" 'Long'")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.STRING, TokenKind.STRING]
    assert [t.value for t in body] == ["Length", "Long"]


def test_string_escape_of_quote_and_backslash() -> None:
    tokens = tokenize(r'"a\"b\\c"')
    assert tokens[0].value == 'a"b\\c'


def test_unterminated_string_raises_with_position() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        tokenize('x = "abc')
    assert exc.value.line == 1
    assert exc.value.col == 5


def test_line_comment_is_skipped() -> None:
    tokens = tokenize("len = 14 // comment\nplot(len)")
    values = [t.value for t in tokens if t.kind is not TokenKind.EOF]
    assert "//" not in values
    assert "comment" not in values


def test_newline_emitted_at_top_level_between_statements() -> None:
    kinds = _kinds("len = 14\nplot(len)")
    assert TokenKind.NEWLINE in kinds
    assert kinds.count(TokenKind.NEWLINE) == 1


def test_newline_suppressed_inside_parens_for_multiline_calls() -> None:
    kinds = _kinds("plot(\n  1\n)")
    assert TokenKind.NEWLINE not in kinds


def test_unexpected_character_raises_with_position() -> None:
    with pytest.raises(PineSyntaxError) as exc:
        tokenize("x = 1 ? 2 : 3")
    assert exc.value.line == 1
    assert exc.value.col == 7


def test_unterminated_string_raises_when_newline_precedes_closing_quote() -> None:
    """negative: 문자열이 줄바꿈을 넘어가면(닫는 따옴표가 다음 줄에 있어도) 조용히
    여러 줄 문자열로 통과시키지 않고 시작 위치에서 거부한다 — EOF로 끝나는 경우
    (`test_unterminated_string_raises_with_position`)와는 다른 코드 경로다."""
    with pytest.raises(PineSyntaxError) as exc:
        tokenize('"abc\ndef"')
    assert exc.value.line == 1
    assert exc.value.col == 1


def test_unexpected_character_raises_with_position_on_later_line() -> None:
    """negative: 오류 위치 추적이 첫 줄 이후에도 정확해야 한다 — 변환 실패 시 위치
    정보 없이 조용히 무시하지 않는다는 불변식(DSL-14)을 여러 줄 소스로 확인한다."""
    with pytest.raises(PineSyntaxError) as exc:
        tokenize("a = 1\nb = @")
    assert exc.value.line == 2
    assert exc.value.col == 5


def test_internal_dependency_failure_propagates_instead_of_silent_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: 식별자 시작 판별 헬퍼가 내부 예외를 던지면 `tokenize`는 이를 삼켜
    부분/손상된 토큰 스트림을 성공으로 위장하지 않고 그대로 전파해야 한다
    (fail-closed)."""

    def boom(ch: str) -> bool:
        raise RuntimeError("dependency failure")

    monkeypatch.setattr(pine_lexer, "_is_ident_start", boom)

    with pytest.raises(RuntimeError):
        pine_lexer.tokenize("close")


@pytest.mark.perf
def test_tokenize_throughput_within_dsl_compile_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: DSL 컴파일 예산 300ms(ADR-2026-09-09-C Decision 1) 중 렉싱 단계가
    차지할 수 있는 상한을 실사용 스크립트보다 훨씬 큰 2000줄 합성 소스로 검증한다.
    task-7434: process_time 기반 perf_budget으로 측정한다."""
    source = "\n".join(f"len_{i} = {i} + close * {i}.5" for i in range(2000))
    tokens: list = []

    def _run_once() -> None:
        nonlocal tokens
        tokens = tokenize(source)

    perf_budget.assert_within(_run_once, budget_ms=300.0, label="tokenize 2000-line source")
    assert tokens[-1].kind is TokenKind.EOF
