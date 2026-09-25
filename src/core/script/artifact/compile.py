"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-12 —
Assembles the AIOS Script compile pipeline (pure, no I/O).

Calls DSL-2 lexer -> DSL-3 parser -> DSL-4 type -> DSL-5 lookahead -> DSL-6
resource -> DSL-7 IR in that exact order (task-1535 spec wording). Each
stage is not reimplemented; only that stage's public function is called.
On success this yields a `CompiledScript` (IR, IR bytes, resource estimate,
`script_hash`); on failure it wraps one of the §3.3 taxonomy's 4 kinds
(`SCRIPT_SYNTAX`, `SCRIPT_TYPE`, `SCRIPT_LOOKAHEAD`, `SCRIPT_RESOURCE_LIMIT`)
into a single `ScriptCompileError` together with (line, col).

Error position recovery (§3.3 "includes position info"): lexer, parser, and
lookahead errors already carry a token position. Type and resource errors
have no position on the AST (DSL-1 decision), so they are recovered via
"declaration-prefix binary search" — type checking walks declarations in
source order and stops at the first error, and resource estimates are all
monotonically non-decreasing as declarations accumulate, so the k-th
declaration at the smallest k where `Program(decls[:k])` fails is the
cause. Grammar requires every declaration to start with one of the
`input|let|plot|signal|order` keywords, and that keyword can never appear
inside an expression, so the (line, col) of the k-th declaration's leading
keyword token is exactly the declaration's position.

Consequence of the stage order: negative or variable postfix indices are
rejected by the parser (DSL-3) as `SCRIPT_SYNTAX` before lookahead ever
runs. `SCRIPT_LOOKAHEAD` only fires on forms that pass the parser (e.g.
`ta.security(...)`-style calls). `ScriptLowerError` (DSL-7) is outside the
taxonomy — a §3.3 AST that has passed type checking must always lower
successfully, so a lowering failure is a contract violation; it is not
wrapped here and propagates as-is (500 INTERNAL_ERROR at the API layer).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_lookahead
from src.core.script.analysis.resources import (
    DEFAULT_LIMITS,
    ResourceEstimate,
    ResourceLimits,
    ScriptResourceLimitError,
    check_resources,
)
from src.core.script.artifact.hash import script_hash
from src.core.script.grammar.ast import GRAMMAR_VERSION, Program
from src.core.script.grammar.lexer import ScriptSyntaxError, Token, TokenKind, tokenize
from src.core.script.grammar.parser import parse
from src.core.script.ir.lower import lower_program
from src.core.script.ir.ops import IR_VERSION, IRProgram, to_bytes
from src.core.script.typing.checker import ScriptTypeError, check_program

SCRIPT_ERROR_CODES: Final = frozenset(
    {"SCRIPT_SYNTAX", "SCRIPT_TYPE", "SCRIPT_LOOKAHEAD", "SCRIPT_RESOURCE_LIMIT"}
)
_DECL_KEYWORDS: Final = frozenset({"input", "let", "plot", "signal", "order"})


class ScriptCompileError(Exception):
    """A compile error wrapping one of the §3.3 taxonomy's 4 kinds (400, not retryable).

    `code` is one of the 4 kinds; `line`/`col` are 1-based positions.
    `details` is the dict (`code`/`line`/`col`) carried as-is in the API
    envelope's `ApiError.details` — the top-level error_code stays within
    the existing taxonomy (`VALIDATION_INVALID_FIELD`).
    """

    def __init__(self, code: str, message: str, line: int, col: int) -> None:
        if code not in SCRIPT_ERROR_CODES:
            raise ValueError(f"code outside §3.3 taxonomy: {code!r}")
        super().__init__(f"[{code}] {message} (line {line}, col {col})")
        self.code = code
        self.message = message
        self.line = line
        self.col = col
        self.details: dict[str, Any] = {"code": code, "line": line, "col": col}


@dataclass(frozen=True)
class CompiledScript:
    source: str
    ir: IRProgram
    ir_bytes: bytes
    estimate: ResourceEstimate
    registry_version: str
    script_hash: str
    grammar_version: str = GRAMMAR_VERSION
    ir_version: str = IR_VERSION


def compile_source(
    source: str,
    *,
    registry_version: str,
    limits: ResourceLimits = DEFAULT_LIMITS,
) -> CompiledScript:
    """Source -> `CompiledScript`. On failure, raises one `ScriptCompileError` (one of 4 kinds)."""
    try:
        tokens = tokenize(source)  # DSL-2
        program = parse(source)  # DSL-3
    except ScriptSyntaxError as exc:
        raise ScriptCompileError(exc.code, exc.message, exc.line, exc.col) from exc
    try:
        check_program(program)  # DSL-4
    except ScriptTypeError as exc:
        line, col = _locate_failing_decl(tokens, program, _type_fails)
        raise ScriptCompileError(exc.code, exc.message, line, col) from exc
    try:
        check_lookahead(tokens)  # DSL-5
    except ScriptLookaheadError as exc:
        raise ScriptCompileError(exc.code, exc.message, exc.line, exc.col) from exc
    try:
        estimate = check_resources(program, limits)  # DSL-6
    except ScriptResourceLimitError as exc:
        line, col = _locate_failing_decl(tokens, program, _resource_fails(limits))
        raise ScriptCompileError(exc.code, exc.message, line, col) from exc
    ir = lower_program(program)  # DSL-7 -- ScriptLowerError is not wrapped (see module docstring)
    ir_bytes = to_bytes(ir)
    digest = script_hash(source=source, ir=ir, registry_version=registry_version)
    return CompiledScript(
        source=source,
        ir=ir,
        ir_bytes=ir_bytes,
        estimate=estimate,
        registry_version=registry_version,
        script_hash=digest,
    )


# ---- position recovery ----


def _type_fails(prefix: Program) -> bool:
    try:
        check_program(prefix)
    except ScriptTypeError:
        return True
    return False


def _resource_fails(limits: ResourceLimits) -> Callable[[Program], bool]:
    def fails(prefix: Program) -> bool:
        try:
            check_resources(prefix, limits)
        except ScriptResourceLimitError:
            return True
        return False

    return fails


def decl_positions(tokens: list[Token]) -> list[tuple[int, int]]:
    """List of (line, col) for each decl's leading keyword token, 1:1 with declaration count."""
    return [
        (tok.line, tok.col)
        for tok in tokens
        if tok.kind is TokenKind.KEYWORD and tok.value in _DECL_KEYWORDS
    ]


def _locate_failing_decl(
    tokens: list[Token], program: Program, fails: Callable[[Program], bool]
) -> tuple[int, int]:
    """Binary-searches the smallest 1-based k where `fails(Program(decls[:k]))`
    holds and returns that declaration's position. See the module docstring
    for the prefix-monotonicity argument. If `fails` is false even over the
    full program (a caller contract violation), fail-closed to (1, 1)."""
    positions = decl_positions(tokens)
    n = len(program.decls)
    if n == 0 or len(positions) != n:
        return (1, 1)
    lo, hi = 1, n
    while lo < hi:
        mid = (lo + hi) // 2
        if fails(Program(decls=program.decls[:mid])):
            hi = mid
        else:
            lo = mid + 1
    if not fails(Program(decls=program.decls[:lo])):
        return (1, 1)
    return positions[lo - 1]


__all__ = [
    "SCRIPT_ERROR_CODES",
    "CompiledScript",
    "ScriptCompileError",
    "compile_source",
    "decl_positions",
]
