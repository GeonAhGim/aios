"""L4_analytics_authoring_backtest_marketplace_v1.0.md M2-3 step 2 (task-8694) —
AIOS Script DSL `array<float>` type.

`array<float>` is a 7th type (grammar/ast.py `TypeName`), a constant-length
vector literal (`[1.0, 2.0, 3.0]`). Unlike `series<float>`(a per-bar runtime
buffer), it is materialised once at compile time. Deliberately NOT a member of
`NUMERIC_TYPES` (`typing/types.py`) so every existing arithmetic/comparison
promotion function already rejects it without new branches, and it has no
implicit conversion to/from `series<float>`. Resource accounting
(`analysis/resources.py`) sums literal element counts into a new
`array_length` metric checked against `max_array_length`.

Covers: (1) lexer/parser accept `array<float>` type annotations and `[...]`
literal syntax (same production pattern as `series<...>`), (2) AST round trip
is identity, (3) `types.py` ARRAY_TYPES/is_array/element_type, (4) negative:
arithmetic misuse rejected, (5) negative: length-limit exceeded rejected at
compile time, (6) negative: implicit conversion to/from series<float>
rejected.
"""

from __future__ import annotations

import pytest

from src.core.script.analysis.resources import (
    ResourceLimits,
    ScriptResourceLimitError,
    check_resources,
)
from src.core.script.grammar.ast import (
    ArrayLiteral,
    NumberLiteral,
    Program,
    TypeNode,
    program_from_dict,
    to_dict,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, TokenKind, tokenize
from src.core.script.grammar.parser import parse
from src.core.script.typing.checker import ScriptTypeError, check_program
from src.core.script.typing.types import (
    ARRAY_TYPES,
    NUMERIC_TYPES,
    element_type,
    is_array,
)

# ---- lexer: "array" tokenizes as an ordinary IDENT (same as "series") ----


def test_lexer_tokenizes_array_as_ident_not_a_reserved_type_word() -> None:
    tokens = tokenize("array<float>")
    body = tokens[:-1]
    assert body[0].kind is TokenKind.IDENT
    assert body[0].value == "array"


# ---- ast: ArrayLiteral / TypeNode(name="array<float>") are valid, roundtrip is identity ----


def test_array_literal_node_has_array_kind_and_elements() -> None:
    node = ArrayLiteral(elements=(NumberLiteral(value=1.0), NumberLiteral(value=2.0)))
    assert node.kind == "array"
    assert len(node.elements) == 2


def test_type_node_accepts_array_float_type_name() -> None:
    node = TypeNode(name="array<float>")
    assert node.name == "array<float>"


def test_array_literal_to_dict_program_from_dict_roundtrip_is_identity() -> None:
    program = parse("let v = [1.0, 2.0, 3.0]")
    data = to_dict(program)
    restored = program_from_dict(data)
    assert restored == program
    assert isinstance(restored, Program)
    assert to_dict(restored) == data


# ---- types.py: array<float> is its own set, not numeric, element_type is float ----


def test_array_float_is_in_its_own_type_set_only() -> None:
    assert "array<float>" in ARRAY_TYPES
    assert "array<float>" not in NUMERIC_TYPES
    assert is_array("array<float>") is True
    assert is_array("float") is False


def test_element_type_of_array_float_is_float() -> None:
    assert element_type("array<float>") == "float"


# ---- parser: array literal parses as a primary expr, type annotation parses ----


def test_parser_accepts_array_literal_as_primary_expr() -> None:
    program = parse("let v = [1.0, 2.0, 3.0]")
    decl = program.decls[0]
    assert isinstance(decl.expr, ArrayLiteral)
    assert [e.value for e in decl.expr.elements] == [1.0, 2.0, 3.0]


def test_parser_accepts_empty_array_literal() -> None:
    program = parse("let v = []")
    assert isinstance(program.decls[0].expr, ArrayLiteral)
    assert program.decls[0].expr.elements == ()


def test_parser_rejects_array_type_with_non_float_inner_type() -> None:
    with pytest.raises(ScriptSyntaxError):
        parse("input v: array<bool> = 0")


# ---- checker: array<float> literal infers correctly, elements must be numeric scalar ----


def test_checker_infers_array_literal_as_array_float() -> None:
    env = check_program(parse("let v = [1.0, 2.0, 3.0]"))
    assert env == {"v": "array<float>"}


def test_checker_rejects_array_literal_with_bool_element() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("input b: bool = 0\nlet v = [b]"))


# ---- negative 1: arithmetic operator misuse is rejected (array is not numeric) ----


def test_checker_rejects_array_used_as_arithmetic_operand() -> None:
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse("let v = [1.0, 2.0] + 1.0"))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_checker_rejects_array_used_in_comparison() -> None:
    with pytest.raises(ScriptTypeError):
        check_program(parse("signal s = [1.0] == [1.0]"))


# ---- negative 2: implicit conversion to/from series<float> is rejected ----


def test_checker_rejects_series_element_inside_array_literal() -> None:
    # `close` is series<float>; an array literal element must be a compile-time
    # numeric scalar, not a per-bar runtime series value -- no implicit collapse.
    with pytest.raises(ScriptTypeError) as excinfo:
        check_program(parse("input close: series<float> = 0\nlet v = [close]"))
    assert excinfo.value.code == "SCRIPT_TYPE"


def test_checker_rejects_array_passed_where_series_float_is_required() -> None:
    # plot() requires the numeric lattice (int/float/series<float>); array<float>
    # is deliberately outside it, so this must fail-close, not silently widen.
    with pytest.raises(ScriptTypeError):
        check_program(parse("plot([1.0, 2.0])"))


# ---- negative 3: compile-time resource limit on total array literal length ----


def test_resources_rejects_array_literal_exceeding_max_array_length() -> None:
    limits = ResourceLimits(max_array_length=3)
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(parse("let v = [1.0, 2.0, 3.0, 4.0]"), limits)
    assert excinfo.value.metric == "array_length"


def test_resources_accepts_array_literal_at_exact_length_limit() -> None:
    limits = ResourceLimits(max_array_length=3)
    estimate = check_resources(parse("let v = [1.0, 2.0, 3.0]"), limits)
    assert estimate.array_length == 3


def test_resources_sums_multiple_array_literals_across_decls() -> None:
    limits = ResourceLimits(max_array_length=4)
    program = parse("let a = [1.0, 2.0]\nlet b = [3.0, 4.0, 5.0]")
    with pytest.raises(ScriptResourceLimitError) as excinfo:
        check_resources(program, limits)
    assert excinfo.value.metric == "array_length"
