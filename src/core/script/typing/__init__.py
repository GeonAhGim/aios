"""AIOS Script 정적 타입 시스템 패키지 — 타입 격자(DSL-4 `types.py`)와
DSL-1 AST에 대한 타입 검사기(DSL-4 `checker.py`)."""
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
