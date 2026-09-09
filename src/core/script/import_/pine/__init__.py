"""DSL-14 — Pine Script v5 partial-grammar importer (lexer + parser).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.9 (DSL-14), prerequisite DSL-3 (`grammar/parser.py`, task-1337, 5812ee4).

Uses `import_` because a directory named `import` is a Python reserved word
and cannot be used as a package name (this corresponds to the spec's
`script/import/pine/`). The AST/lexer built here handle a different language
(Pine v5) from the existing AIOS Script grammar
(`src/core/script/grammar/{lexer,parser}.py`), so those modules are neither
reused nor modified.

Conversion (transpile) to AIOS Script is DSL-15's job (`transpile.py`) —
the `PineProgram` `parse()` returns is DSL-14's final output and DSL-15's
input.
"""
from __future__ import annotations

from src.core.script.import_.pine.ast import PineProgram
from src.core.script.import_.pine.lexer import PineSyntaxError, Token, TokenKind, tokenize
from src.core.script.import_.pine.parser import parse
from src.core.script.import_.pine.transpile import (
    PineTranspileError,
    TranspileResult,
    transpile_and_verify,
    transpile_program,
    transpile_source,
)

__all__ = [
    "PineProgram",
    "PineSyntaxError",
    "PineTranspileError",
    "Token",
    "TokenKind",
    "TranspileResult",
    "parse",
    "tokenize",
    "transpile_and_verify",
    "transpile_program",
    "transpile_source",
]
