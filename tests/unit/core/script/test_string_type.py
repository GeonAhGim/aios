"""L4_analytics_authoring_backtest_marketplace_v1.0.md M2-3 step 1 (task-7847) —
AIOS Script DSL `string` constant type.

`string` is a 6th, independent scalar (grammar/ast.py `TypeName`) that never joins
the numeric (int/float/series<float>) or bool (bool/series<bool>) lattice defined
in `typing/types.py` -- it exists only for future constant arguments (alertcondition
message, plot title; not yet wired to any decl in this step). Covers: (1) the
lexer already tokenizes quoted strings and fail-closes on an unterminated one
(M2-2a, unchanged here), (2) `StringLiteral`/`TypeNode(name="string")` are valid
AST shapes with an identity `to_dict`/`program_from_dict` round trip, (3) the
promotion-lattice functions reject `string` on both sides, (4) the parser accepts
a string literal as a `primary` production, and (5) the checker only accepts the
`string` type behind `AIOS_SCRIPT_STRING_TYPE_ENABLED` (default OFF, fail-closed)
and rejects every arithmetic/comparison/logical/indexing use of it regardless of
the flag, since none of those operators' promotion rules ever admit `string`.
"""

from __future__ import annotations

import pytest

from src.core.script.grammar.ast import (
    Program,
    StringLiteral,
    TypeNode,
    program_from_dict,
    to_dict,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, TokenKind, tokenize
from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import STRING_TYPE_ENV_VAR, ScriptTypeError, check_program
from src.core.script.typing.types import (
    BOOL_TYPES,
    NUMERIC_TYPES,
    STRING_TYPES,
    cmp_result,
    is_string,
    promote_bool,
    promote_numeric,
)

# ---- lexer: quoted strings tokenize; unterminated is a lexer-level fail-close ----


def test_lexer_tokenizes_a_quoted_string_literal() -> None:
    tokens = tokenize('"hello world"')
    body = tokens[:-1]
    assert len(body) == 1
    assert body[0].kind is TokenKind.STRING
    assert body[0].value == "hello world"


def test_lexer_rejects_unterminated_string_literal() -> None:
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize('let x = "abc')
    err = excinfo.value
    assert err.code == "SCRIPT_SYNTAX"
    assert err.line == 1
    assert err.col == 9  # opening '"' position


def test_lexer_rejects_string_literal_broken_by_newline() -> None:
    # A bare newline inside the quotes is also "unterminated" (no multi-line
    # strings in this minimal grammar) -- must fail closed, not hang or splice.
    with pytest.raises(ScriptSyntaxError) as excinfo:
        tokenize('let x = "abc\ndef"')
    assert excinfo.value.code == "SCRIPT_SYNTAX"


# ---- ast: StringLiteral / TypeNode(name="string") are valid, roundtrip is identity ----


def test_string_literal_node_has_string_kind_and_value() -> None:
    node = StringLiteral(value="hi")
    assert node.kind == "string"
    assert node.value == "hi"


def test_type_node_accepts_string_type_name() -> None:
    node = TypeNode(name="string")
    assert node.name == "string"


def test_string_literal_to_dict_program_from_dict_roundtrip_is_identity() -> None:
    program = parse('let msg = "hello"')
    data = to_dict(program)
    restored = program_from_dict(data)
    assert restored == program
    assert isinstance(restored, Program)
    assert to_dict(restored) == data


# ---- types.py: string is in neither numeric nor bool lattice, promotions reject it ----


def test_string_is_in_its_own_type_set_only() -> None:
    assert "string" in STRING_TYPES
    assert "string" not in NUMERIC_TYPES
    assert "string" not in BOOL_TYPES
    assert is_string("string") is True
    assert is_string("float") is False


def test_promote_numeric_rejects_string() -> None:
    assert promote_numeric("string", "float") is None
    assert promote_numeric("int", "string") is None
    assert promote_numeric("string", "string") is None


def test_promote_bool_rejects_string() -> None:
    assert promote_bool("string", "bool") is None
    assert promote_bool("bool", "string") is None


def test_cmp_result_rejects_string() -> None:
    assert cmp_result("string", "string") is None
    assert cmp_result("string", "int") is None


# ---- parser: a string literal parses as a `primary` ----


def test_parser_accepts_string_literal_as_primary_expr() -> None:
    program = parse('let msg = "hello world"')
    decl = program.decls[0]
    assert isinstance(decl.expr, StringLiteral)
    assert decl.expr.value == "hello world"


def test_parser_accepts_string_literal_inside_parens() -> None:
    program = parse('let msg = ("hi")')
    assert isinstance(program.decls[0].expr, StringLiteral)


# ---- checker: string type gated by feature flag, default OFF (fail-closed) ----


def test_checker_rejects_string_constant_when_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(STRING_TYPE_ENV_VAR, raising=False)
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse('let msg = "hello"'))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_checker_rejects_string_constant_when_flag_is_falsy_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the exact "1" turns it on -- "true"/"yes"/"on" stay fail-closed,
    # mirroring AIOS_ALLOW_LIVE_ADAPTER's exact-match gate (src/exchanges/factory.py).
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "true")
    with pytest.raises(ScriptTypeError):
        check_program(parse('let msg = "hello"'))


def test_checker_accepts_string_constant_when_flag_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    env = check_program(parse('let msg = "hello"'))
    assert env == {"msg": "string"}


# ---- checker: string never mixes into the numeric/bool lattice, flag on or off ----


def test_checker_rejects_string_arithmetic_even_with_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse('let x = "a" + "b"'))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_checker_rejects_string_comparison_even_with_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    with pytest.raises(ScriptTypeError):
        check_program(parse('signal s = "a" == "a"'))


def test_checker_rejects_string_in_logical_op_even_with_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    with pytest.raises(ScriptTypeError):
        check_program(parse('let x = "a" and "b"'))


def test_checker_rejects_string_postfix_indexing_even_with_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    with pytest.raises(ScriptTypeError):
        check_program(parse('let x = "a"[0]'))


def test_checker_rejects_string_as_plot_argument_even_with_flag_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # plot()/signal()/order() all require the numeric/bool lattice (unchanged by
    # this step) -- `string` is not yet wired to any decl, so it stays rejected here.
    monkeypatch.setenv(STRING_TYPE_ENV_VAR, "1")
    with pytest.raises(ScriptTypeError):
        check_program(parse('plot("title")'))
