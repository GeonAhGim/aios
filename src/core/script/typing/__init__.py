"""AIOS Script static type system package — type lattice (DSL-4 `types.py`) and
type checker for DSL-1 AST (DSL-4 `checker.py`)."""

from __future__ import annotations

from src.core.script.typing.checker import ScriptTypeError, TypeEnv, check_program, infer_type
from src.core.script.typing.types import (
    BOOL_TYPES,
    NUMERIC_TYPES,
    Type,
    cmp_result,
    element_type,
    is_series,
    promote_bool,
    promote_numeric,
)

__all__ = [
    "BOOL_TYPES",
    "NUMERIC_TYPES",
    "ScriptTypeError",
    "Type",
    "TypeEnv",
    "check_program",
    "cmp_result",
    "element_type",
    "infer_type",
    "is_series",
    "promote_bool",
    "promote_numeric",
]
