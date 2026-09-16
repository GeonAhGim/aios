"""DSL-2 렉서 테스트.

DEPTH 감사(task-2727, docs/audit/DEPTH_DSL_IND.md) D2 보강: negative를
3건에서 6건으로 늘리고, 실패 주입(장문+악의적 유니코드 강건성)·수치 성능
단언(토큰화 처리량)·게이트 적색 재현(미인식 문자 가드 제거 시뮬레이션)을
추가한다.
"""

import inspect
import time

import pytest

from src.core.script.grammar.lexer import (
    _DELIMS,
    _ONE_CHAR_OPS,
    _TWO_CHAR_OPS,
    KEYWORDS,
    TYPE_WORDS,
    ScriptSyntaxError,
    Token,
    TokenKind,
    _is_ident_cont,
    _is_ident_start,
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
        "input length: int = 14\nlet rsi_val = ta.rsi(close, length)\nsignal go_long = rsi_val < 30"
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


# --- DEPTH 감사 D2 보강: negative 확장(3건 -> 6건) ---------------------------


def test_invalid_character_at_start_of_source_reports_line1_col1():
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("@ x")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 1


def test_unterminated_decimal_mid_expression_raises_at_dot_position():
    # EOF 직전(`3.`)뿐 아니라 식 중간(`3.x`)에서도 소수점 뒤 숫자 없음이
    # 같은 규칙으로 거부되는지 확인한다 — "EOF 특수 케이스"가 아님을 증명.
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("let x = 3.x")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 10  # '.' 의 위치


def test_invalid_emoji_character_rejected_with_correct_column():
    # 코드 포인트 하나로 계산되는 non-BMP 문자(이모지)도 col 계산이 바이트가
    # 아니라 문자 단위로 정확해야 한다.
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize("let x = 🚀")
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 9


# --- DEPTH 감사 D2 보강: 실패 주입(장문 + 악의적 유니코드 강건성) -----------


def test_tokenize_survives_pathological_input_without_crashing_or_hanging():
    """실패 주입(DEPTH 감사 예시): 매우 긴 입력과 악의적 유니코드를 섞어
    tokenize의 강건성을 확인한다. 두 시나리오 모두 RecursionError/IndexError
    같은 예기치 못한 크래시나 무한 루프 없이, 유한 시간 안에 정상 토큰화되거나
    ScriptSyntaxError(fail-closed)로 끝나야 한다."""
    # 시나리오 1: 매우 긴 유효 입력(5만 개 식별자) — 정상 토큰화, 시간 예산 내.
    huge_valid_source = " ".join(f"x{i}" for i in range(50_000))
    start = time.perf_counter()
    tokens = tokenize(huge_valid_source)
    elapsed = time.perf_counter() - start
    assert tokens[-1].kind is TokenKind.EOF
    assert len(tokens) == 50_001  # 식별자 5만 개 + EOF
    assert elapsed < 3.0, f"대용량 입력 처리 {elapsed:.3f}s > 3.0s 예산(무한루프/크래시 의심)"

    # 시나리오 2: 악의적 유니코드(NUL, RTL override, ZWSP, BOM)는 크래시 없이
    # 항상 SCRIPT_SYNTAX(fail-closed)로 깔끔히 거부된다. 원 소스 파일에 제어
    # 문자를 직접 박아 넣지 않도록 chr()로 런타임에 구성한다.
    nul = chr(0)
    rtl_override = chr(0x202E)
    zero_width_space = chr(0x200B)
    bom = chr(0xFEFF)
    for malicious_char in (nul, rtl_override, zero_width_space, bom):
        with pytest.raises(ScriptSyntaxError) as excinfo:
            tokenize(f"let x = {malicious_char}")
        assert excinfo.value.code == "SCRIPT_SYNTAX"

    # 시나리오 3: 악의적 유니코드가 아주 긴 유효 입력 뒤에 섞여도(초반에 오래
    # 걸리지 않고) 여전히 유한 시간 안에 거부된다.
    combined = huge_valid_source + " " + rtl_override
    start = time.perf_counter()
    with pytest.raises(ScriptSyntaxError):
        tokenize(combined)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0, f"장문+악성 유니코드 처리 {elapsed:.3f}s > 3.0s 예산"


# --- DEPTH 감사 D2 보강: 수치 성능 단언(토큰화 처리량) ----------------------


def test_tokenize_throughput_meets_budget():
    """수치 성능 단언: 현실적인 스크립트 반복 패턴에 대해 tokenize 처리량이
    최소 500,000 chars/s(실측 약 400만 chars/s 대비 8배 여유)를 넘어야 한다
    — 우연한 이차 복잡도 회귀(예: 문자열 슬라이스 남용)를 조기에 검출한다."""
    line = "let rsi_val_{i} = ta.rsi(close, length) # comment\n"
    source = "".join(line.format(i=i) for i in range(2_000))

    for _ in range(5):  # 워밍업 — 첫 호출 지연을 측정에서 배제
        tokenize(source)

    iterations = 20
    start = time.perf_counter()
    for _ in range(iterations):
        tokens = tokenize(source)
    elapsed_s = time.perf_counter() - start

    throughput_chars_per_s = (len(source) * iterations) / elapsed_s
    assert tokens[-1].kind is TokenKind.EOF
    assert throughput_chars_per_s >= 500_000, (
        f"토큰화 처리량 {throughput_chars_per_s:.0f} chars/s < 500,000 chars/s 예산"
    )


# --- DEPTH 감사 D2 보강: 게이트 적색 재현 -----------------------------------


def test_removing_unknown_character_guard_would_make_gate_red():
    """게이트 적색 재현: `tokenize` 마지막 분기(미인식 문자 -> fail-closed
    ScriptSyntaxError)를 실제 소스에서 패치 제거한 회귀 버전을 exec로 만들어,
    그 회귀가 들어오면 악의적/손상된 입력이 예외 없이 조용히 통과함(가드가
    빨간불이 됨)을 증명한다. 실제 tokenize는 동일 입력을 여전히 거부한다."""
    original_source = inspect.getsource(tokenize)
    guard_line = (
        '        raise ScriptSyntaxError(f"예상치 못한 문자 {ch!r}", start_line, start_col)'
    )
    assert guard_line in original_source, "가드 라인 텍스트가 바뀌어 회귀 재현이 무효화됨"

    regressed_source = original_source.replace(
        guard_line,
        "        pos += 1\n        col += 1\n        continue"
        "  # ratchet-regression: guard removed for gate-red repro",
    )
    assert regressed_source != original_source

    namespace: dict[str, object] = {
        "Token": Token,
        "TokenKind": TokenKind,
        "ScriptSyntaxError": ScriptSyntaxError,
        "KEYWORDS": KEYWORDS,
        "TYPE_WORDS": TYPE_WORDS,
        "_TWO_CHAR_OPS": _TWO_CHAR_OPS,
        "_ONE_CHAR_OPS": _ONE_CHAR_OPS,
        "_DELIMS": _DELIMS,
        "_is_ident_start": _is_ident_start,
        "_is_ident_cont": _is_ident_cont,
    }
    exec(compile(regressed_source, "<regressed_tokenize>", "exec"), namespace)  # noqa: S102
    regressed_tokenize = namespace["tokenize"]

    malicious = "let x = @"

    # 회귀 버전: 가드가 빠지면 미인식 문자가 예외 없이 조용히 사라진다
    # — 이 라인 자체가 "가드 없이는 이 테스트가 통과함(빨간불 시나리오)"을 보인다.
    regressed_tokens = regressed_tokenize(malicious)
    assert regressed_tokens[-1].kind is TokenKind.EOF
    assert "@" not in [t.value for t in regressed_tokens]

    # 실제 tokenize는 fail-closed로 동일 입력을 여전히 거부한다.
    with pytest.raises(ScriptSyntaxError):
        tokenize(malicious)
