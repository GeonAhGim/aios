import pytest

from src.core.script.grammar.lexer import (
    KEYWORDS,
    TYPE_WORDS,
    ScriptSyntaxError,
    TokenKind,
    tokenize,
)


def _kinds(source: str) -> list[TokenKind]:
    return [tok.kind for tok in tokenize(source)]


def _values(source: str) -> list[str]:
    return [tok.value for tok in tokenize(source)]


def test_every_keyword_tokenizes_as_keyword():
    source = " ".join(sorted(KEYWORDS))
    tokens = tokenize(source)
    body = tokens[:-1]  # 마지막은 EOF
    assert len(body) == len(KEYWORDS)
    for tok in body:
        assert tok.kind is TokenKind.KEYWORD
        assert tok.value in KEYWORDS


def test_every_type_word_tokenizes_as_type():
    source = " ".join(sorted(TYPE_WORDS))
    tokens = tokenize(source)
    body = tokens[:-1]
    assert len(body) == len(TYPE_WORDS)
    for tok in body:
        assert tok.kind is TokenKind.TYPE
        assert tok.value in TYPE_WORDS


def test_namespace_and_generic_identifiers():
    tokens = tokenize("ta math series my_var2")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.IDENT] * 4
    assert [t.value for t in body] == ["ta", "math", "series", "my_var2"]


def test_integer_and_float_numbers():
    tokens = tokenize("14 0.5 100")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.NUMBER] * 3
    assert [t.value for t in body] == ["14", "0.5", "100"]


def test_all_comparison_and_arithmetic_operators():
    tokens = tokenize("< <= == >= > + - * /")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.OP] * 9
    assert [t.value for t in body] == ["<", "<=", "==", ">=", ">", "+", "-", "*", "/"]
    assert [t.subtype for t in body] == [
        "LT",
        "LE",
        "EQEQ",
        "GE",
        "GT",
        "PLUS",
        "MINUS",
        "STAR",
        "SLASH",
    ]


def test_all_delimiters():
    tokens = tokenize("( ) [ ] , : = .")
    body = tokens[:-1]
    assert [t.kind for t in body] == [TokenKind.DELIM] * 8
    assert [t.value for t in body] == ["(", ")", "[", "]", ",", ":", "=", "."]


def test_comments_and_whitespace_are_skipped_not_tokenized():
    tokens = tokenize("let x = 1 # this is a comment\nlet y = 2")
    values = [t.value for t in tokens if t.kind is not TokenKind.EOF]
    assert "#" not in values
    assert "this" not in values
    assert values == ["let", "x", "=", "1", "let", "y", "=", "2"]


def test_series_generic_type_is_ident_lt_type_gt_not_a_single_token():
    # §3.3: type := ... | "series<float>" | "series<bool>". 렉서는 문맥을 모른 채
    # 문자만 보므로 IDENT("series") OP("<") TYPE("float") OP(">")로 쪼갠다 —
    # 파서가 이 네 토큰을 하나의 타입으로 조립한다(decision: 파싱은 여기서 안 함).
    tokens = tokenize("series<float>")
    body = tokens[:-1]
    assert [t.kind for t in body] == [
        TokenKind.IDENT,
        TokenKind.OP,
        TokenKind.TYPE,
        TokenKind.OP,
    ]
    assert [t.value for t in body] == ["series", "<", "float", ">"]


def test_assign_versus_equality_are_distinct_tokens():
    assign_tok = tokenize("=")[0]
    eq_tok = tokenize("==")[0]
    assert assign_tok.kind is TokenKind.DELIM
    assert assign_tok.value == "="
    assert eq_tok.kind is TokenKind.OP
    assert eq_tok.value == "=="


def test_eof_token_is_always_last():
    tokens = tokenize("let x = 1")
    assert tokens[-1].kind is TokenKind.EOF
    assert tokenize("")[-1].kind is TokenKind.EOF


def test_realistic_script_snippet_full_token_sequence():
    source = (
        "input length: int = 14\n"
        "let rsi_val = ta.rsi(close, length)\n"
        "signal go_long = rsi_val < 30"
    )
    tokens = tokenize(source)
    kinds = [t.kind for t in tokens]
    assert kinds[0] is TokenKind.KEYWORD  # input
    assert TokenKind.TYPE in kinds  # int
    assert TokenKind.IDENT in kinds  # ta, rsi, close, length, ...
    assert TokenKind.NUMBER in kinds  # 14, 30
    assert TokenKind.DELIM in kinds  # : = ( , )
    assert TokenKind.OP in kinds  # <
    assert kinds[-1] is TokenKind.EOF


def test_line_and_col_track_real_position_across_lines():
    source = "let a = 1\nlet b = 2\n  let c = 3"
    tokens = {t.value: (t.line, t.col) for t in tokenize(source) if t.value in ("a", "b", "c")}
    assert tokens["a"] == (1, 5)
    assert tokens["b"] == (2, 5)
    assert tokens["c"] == (3, 7)  # 2칸 들여쓰기


def test_position_is_not_always_1_1():
    tokens = tokenize("let a = 1\nlet b = 2")
    positions = {(t.line, t.col) for t in tokens}
    assert len(positions) > 1
    assert any(line != 1 for line, _ in positions)


def test_crlf_newline_advances_line_number():
    tokens = tokenize("let a = 1\r\nlet b = 2")
    b_tok = next(t for t in tokens if t.value == "b")
    assert b_tok.line == 2


def test_invalid_character_raises_script_syntax_with_position():
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("let x = 1\nlet y = @")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 2
    assert err.col == 9


def test_unterminated_number_literal_raises_script_syntax_with_position():
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("let x = 3.")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 10


def test_invalid_character_mid_expression_reports_exact_column():
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("a & b")
    err = excinfo.value
    assert err.line == 1
    assert err.col == 3
