"""L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3/§9.4 DSL-5 —
AIOS Script lookahead and repaint pattern static detector.

Spec: §2 table row 85 (`analysis/lookahead.py`, 240-line cap), §3 row 168
("at compile time, lookahead.py statically verifies series offset sign and
absence of security()-like calls (violation = SCRIPT_LOOKAHEAD)"),
§9.4 DoD ("reject negative index, variable index, future functions").

Accepts only the token stream produced by DSL-2 (`grammar/lexer.py`)
`tokenize()` — does not reference the DSL-1 `Program` AST
(`grammar/ast.py`). Two reasons:

1. Position. The §3.3 error taxonomy requires (line, col) even for
   `SCRIPT_LOOKAHEAD` violations, but `ScriptNode` has no position field
   (this is an already-stated constraint and decision in DSL-4
   `typing/checker.py`, which also cannot emit positions — we do not
   arbitrarily extend the AST owned by another worker this cycle). Tokens
   already carry (line, col) from DSL-2, so reusing them makes
   "matches DSL-2 token position" trivially true by design.
2. Type independence. The INT in `postfix := primary "[" INT "]"` is
   already fixed by grammar to "non-negative integer constant"
   (DSL-1 `PostfixExpr.index: int | None = Field(ge=0)`), and every form
   outside that ("[-1]", "[i]", "[n+1]", etc.) is rejected regardless of
   the base type (scalar/series) — bringing in DSL-4 TypeEnv would not
   change this decision. Hence we do not consume DSL-4 type information
   (type inference to re-implement is not needed in the first place).

Fail-closed: anything that is not exactly `NUMBER "]"` after "[" is
rejected without further investigation — decision: "the moment uncertain
cases are allowed through, this leaf's reason to exist disappears".
security()-like calls are caught uniformly regardless of whether ns/ident
appears where (`security(...)`, `ta.security(...)`, `security.request(...)`)
because §3.3 grammar only permits the `ns "." ident "(" args ")"` form.

Pure functions — no script execution, I/O, or DB access (DoD (4)).

Unverified: the exact function name list in `_FUTURE_FUNCTION_NAMES` was
inferred from the spec phrase ("security()-like calls") rather than a
specific exchange/vendor document — it references TradingView Pine Script's
`security()`/`request.security()` (the canonical function that pulls
unconfirmed bars from other symbols/timeframes, causing repaint), but is
not a list confirmed by the AIOS Script own registry (DSL-9, not yet
existent).
"""
from __future__ import annotations

from collections.abc import Sequence

from src.core.script.grammar.lexer import Token, TokenKind, tokenize

_FUTURE_FUNCTION_NAMES = frozenset({"security", "request_security"})


class ScriptLookaheadError(Exception):
    """`SCRIPT_LOOKAHEAD` from §3.3 error taxonomy (400, non-retryable)."""

    code = "SCRIPT_LOOKAHEAD"

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def check_source(source: str) -> None:
    """Tokenize an AIOS Script source and apply `check_lookahead`."""
    check_lookahead(tokenize(source))


def check_lookahead(tokens: Sequence[Token]) -> None:
    """Static-detect lookahead and repaint patterns. Raises `ScriptLookaheadError` on violation.

    Only scans the two points where lookahead can occur across the §3.3 grammar —
    other productions are structurally guaranteed deterministic (no loop/recursion/
    external I/O) (see DSL-1 decision):

    - postfix index: reject if the token after "[" is not `NUMBER "]"`.
    - call: reject if either side of `ns "." ident` is in `_FUTURE_FUNCTION_NAMES`.
    """
    for i, tok in enumerate(tokens):
        if tok.kind is TokenKind.DELIM and tok.value == "[":
            _check_index(tokens, i)
        elif tok.kind is TokenKind.IDENT and tok.value in _FUTURE_FUNCTION_NAMES:
            _check_future_call(tokens, i)


def _at(tokens: Sequence[Token], idx: int) -> Token:
    return tokens[idx] if idx < len(tokens) else tokens[-1]  # tokens[-1] == EOF


def _check_index(tokens: Sequence[Token], bracket_idx: int) -> None:
    inner = _at(tokens, bracket_idx + 1)
    closing = _at(tokens, bracket_idx + 2)
    is_plain_constant = (
        inner.kind is TokenKind.NUMBER
        and "." not in inner.value
        and closing.kind is TokenKind.DELIM
        and closing.value == "]"
    )
    if is_plain_constant:
        return
    if inner.kind is TokenKind.OP and inner.value == "-":
        raise ScriptLookaheadError(
            "시리즈 오프셋에 음수 인덱스(미래 참조)는 금지합니다", inner.line, inner.col
        )
    if inner.kind is TokenKind.IDENT:
        raise ScriptLookaheadError(
            "시리즈 오프셋은 상수만 허용합니다"
            "(변수 인덱스는 정적으로 미래 참조가 아님을 증명할 수 없어 거부)",
            inner.line,
            inner.col,
        )
    raise ScriptLookaheadError(
        "시리즈 오프셋이 0 이상 정수 상수 하나가 아닙니다"
        "(정적으로 안전함을 증명할 수 없어 fail-closed 거부)",
        inner.line,
        inner.col,
    )


def _check_future_call(tokens: Sequence[Token], ident_idx: int) -> None:
    nxt = _at(tokens, ident_idx + 1)
    if nxt.kind is TokenKind.DELIM and nxt.value in ("(", "."):
        tok = tokens[ident_idx]
        raise ScriptLookaheadError(
            f"미래 데이터 접근 함수 {tok.value!r} 호출은 금지합니다(security()류)",
            tok.line,
            tok.col,
        )
