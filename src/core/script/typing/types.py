"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-4 —
Static type lattice for AIOS Script.

§3.3 Define only "series/scalar promotion–rejection" (DoD) rules as pure functions
for 5 types: `type := "int" | "float" | "bool" | "series<float>" | "series<bool>"`.
AST traversal and per-decl checking is the responsibility of `checker.py` (same leaf),
so this module does not import I/O or `ast.py` — it reuses only the type name (`TypeName`) itself.

Promotion lattice:
    int ≤ float ≤ series<float>   (numeric series — arithmetic/comparison operands)
    bool ≤ series<bool>            (boolean series — logical operation operands)
The two series do not mix (no implicit conversion between bool and numeric — "rejection").

M2-3 step 1 (task-7847): `string` belongs to neither lattice above -- it is
only in `STRING_TYPES`, has no series form, and every arithmetic/comparison/
logical promotion function rejects it on either side.

M2-3 step 2 (task-8694): `array<float>` is a third, separate scalar-adjacent
type -- a constant-length vector literal. It deliberately is NOT a member of
`NUMERIC_TYPES` (arithmetic/comparison/logical promotion all key off that
frozenset membership, so leaving it out is what makes `arr + 1.0` a rejection
without any new branch in `promote_numeric`/`cmp_result`), and it has no
implicit conversion to/from `series<float>` for the same reason.
"""

from __future__ import annotations

from src.core.script.grammar.ast import TypeName

Type = TypeName

NUMERIC_TYPES: frozenset[Type] = frozenset({"int", "float", "series<float>"})
BOOL_TYPES: frozenset[Type] = frozenset({"bool", "series<bool>"})
STRING_TYPES: frozenset[Type] = frozenset({"string"})
ARRAY_TYPES: frozenset[Type] = frozenset({"array<float>"})

_SERIES_TYPES: frozenset[Type] = frozenset({"series<float>", "series<bool>"})
_SERIES_ELEMENT: dict[Type, Type] = {"series<float>": "float", "series<bool>": "bool"}
_ARRAY_ELEMENT: dict[Type, Type] = {"array<float>": "float"}


def is_series(type_: Type) -> bool:
    """Whether `type_` is a series type (series<float>/series<bool>)."""
    return type_ in _SERIES_TYPES


def is_string(type_: Type) -> bool:
    """Whether `type_` is `string` (an independent scalar, not numeric/bool)."""
    return type_ in STRING_TYPES


def is_array(type_: Type) -> bool:
    """Whether `type_` is an array type (currently only `array<float>`)."""
    return type_ in ARRAY_TYPES


def element_type(type_: Type) -> Type:
    """Element type of a series/array type (series<float> -> float, array<float> -> float).

    If a scalar type is passed, return it unchanged (the caller's usual pattern is to
    branch on `is_series`/`is_array` first, but this function itself is not a partial).
    """
    if type_ in _SERIES_ELEMENT:
        return _SERIES_ELEMENT[type_]
    return _ARRAY_ELEMENT.get(type_, type_)


def promote_numeric(left: Type, right: Type) -> Type | None:
    """Result type of arithmetic (+·-·*·/). Promotion order: series<float> > float > int.

    If either operand is not numeric (`NUMERIC_TYPES`), promotion fails and `None`
    is returned instead (e.g., arithmetic with bool).
    """
    if left not in NUMERIC_TYPES or right not in NUMERIC_TYPES:
        return None
    if "series<float>" in (left, right):
        return "series<float>"
    if "float" in (left, right):
        return "float"
    return "int"


def promote_bool(left: Type, right: Type) -> Type | None:
    """Result type of logical operations (and·or). Promotion order: series<bool> > bool.

    If either operand is not boolean (`BOOL_TYPES`), return `None` (rejection).
    """
    if left not in BOOL_TYPES or right not in BOOL_TYPES:
        return None
    return "series<bool>" if "series<bool>" in (left, right) else "bool"


def cmp_result(left: Type, right: Type) -> Type | None:
    """Result type of comparison (<·<=·==·>=·>·crosses_above·crosses_below).

    Both operands must be numeric (`None` on mismatch — rejection). Result is `series<bool>`
    if either is a series (element-wise judgment), else `bool` if both are scalar.
    """
    if left not in NUMERIC_TYPES or right not in NUMERIC_TYPES:
        return None
    return "series<bool>" if is_series(left) or is_series(right) else "bool"
