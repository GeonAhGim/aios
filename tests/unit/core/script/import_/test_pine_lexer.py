"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-14 —
`import_/pine/lexer.py` 테스트.

직접 작성한 최소 Pine 조각만 사용한다(task-2307 decision: TradingView
코드·예제 원문을 저장소에 반입하지 않는다).
"""
from __future__ import annotations

import pytest

from src.core.script.import_.pine.lexer import KEYWORDS, PineSyntaxError, TokenKind, tokenize


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
    tokens = tokenize('"Length" \'Long\'')
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
