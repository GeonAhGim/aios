"""DSL-2 — AIOS Script lexer.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.3(GRAMMAR_VERSION="aios-script-1" grammar), §9.4(DSL-2), §2.4 table(soft cap 260 lines).

Tokenises only — parsing and AST assembly belong to DSL-3 (`grammar/parser.py`)
(decision, task-1235: stop at token level without importing ast.py). `tokenize()` is
a pure function that accepts a single source string, returns a `Token` list, or raises
`ScriptSyntaxError` — no I/O, no global state, no randomness, no clock.

Unverified: §3.3 does not specify comment markers. Because this repository is entirely
Python, we adopt `#` line comments as convention — if DSL-3/the parser leaf later
chooses a different marker, only this file needs updating (comment scanning is
isolated to `_skip_trivia` alone).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

# §3.3 grammar reserved words. ta/math/series (namespaces) are ordinary idents
# in the grammar; the parser interprets them as "ns '.' ident", so we do not
# treat them as reserved words here.
KEYWORDS = frozenset(
    {
        "input",
        "let",
        "plot",
        "signal",
        "order",
        "when",
        "and",
        "or",
        "not",
        "crosses_above",
        "crosses_below",
        "request",
    }
)

# type := "int" | "float" | "bool" | "series<float>" | "series<bool>" —
# only the 3 scalar atoms are reserved words. Compound notations like
# "series<float>" remain as 4 tokens: IDENT("series") LT IDENT("float") GT,
# letting the parser assemble them (the lexer sees characters only, without
# context — identical token stream to a comparison like "series < a").
TYPE_WORDS = frozenset({"int", "float", "bool"})

_TWO_CHAR_OPS = {"<=": "LE", "==": "EQEQ", ">=": "GE"}
_ONE_CHAR_OPS = {"<": "LT", ">": "GT", "+": "PLUS", "-": "MINUS", "*": "STAR", "/": "SLASH"}
_DELIMS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ":": "COLON",
    "=": "ASSIGN",
    ".": "DOT",  # needed for ns "." ident call notation (call rule) — §3.3
    # delimiter list is a summary and omits ".", but the call rule in the
    # grammar body requires it.
}


class TokenKind(enum.Enum):
    KEYWORD = "KEYWORD"
    TYPE = "TYPE"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OP"
    DELIM = "DELIM"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    """`value` is the lexeme string verbatim from source — converting numbers
    to int/float or further subdividing operator kinds belongs to the parser/type
    checker. `subtype` carries a label from `_ONE_CHAR_OPS`, etc., only for OP/DELIM,
    letting the parser branch without string comparison (for KEYWORD/TYPE/IDENT/NUMBER,
    `value` itself is already the unique discriminator, so subtype is an empty string)."""

    kind: TokenKind
    value: str
    subtype: str
    line: int
    col: int


class ScriptSyntaxError(Exception):
    """§3.3 error taxonomy: `SCRIPT_SYNTAX`(400, non-retryable) — the only code
    the lexer can produce (TYPE/LOOKAHEAD/RESOURCE_LIMIT are for later
    compilation stages, decision: do not extend the taxonomy at this leaf)."""

    code = "SCRIPT_SYNTAX"

    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} (line {line}, col {col})")
        self.message = message
        self.line = line
        self.col = col


def _is_ident_start(ch: str) -> bool:
    return ch.isalpha() or ch == "_"


def _is_ident_cont(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def tokenize(source: str) -> list[Token]:
    """Tokenise an AIOS Script source string. The last token is always
    `TokenKind.EOF` (so the parser can detect the end without a special case
    during lookahead)."""
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    n = len(source)

    def peek(offset: int = 0) -> str:
        idx = pos + offset
        return source[idx] if idx < n else ""

    while pos < n:
        ch = source[pos]

        if ch == "\r":
            if peek(1) == "\n":
                pos += 2
            else:
                pos += 1
            line += 1
            col = 1
            continue
        if ch == "\n":
            pos += 1
            line += 1
            col = 1
            continue
        if ch in (" ", "\t"):
            pos += 1
            col += 1
            continue
        if ch == "#":
            while pos < n and source[pos] not in ("\n", "\r"):
                pos += 1
                col += 1
            continue

        start_line, start_col = line, col

        if ch == '"':
            # M2-2a introduced STRING for request(symbol, timeframe, expr); M2-3 step 1
            # (task-7847) reuses the same token for the `string` constant type. §3.3's
            # grammar table has no STRING — minimal grammar with no escapes or newlines
            # (unverified; widen only this point if other uses arise).
            pos += 1
            col += 1
            begin = pos
            while pos < n and source[pos] not in ('"', "\n", "\r"):
                pos += 1
                col += 1
            if pos >= n or source[pos] != '"':
                raise ScriptSyntaxError("미종결 문자열 리터럴", start_line, start_col)
            value = source[begin:pos]
            pos += 1
            col += 1
            tokens.append(Token(TokenKind.STRING, value, "", start_line, start_col))
            continue

        if _is_ident_start(ch):
            begin = pos
            while pos < n and _is_ident_cont(source[pos]):
                pos += 1
                col += 1
            word = source[begin:pos]
            if word in KEYWORDS:
                tokens.append(Token(TokenKind.KEYWORD, word, "", start_line, start_col))
            elif word in TYPE_WORDS:
                tokens.append(Token(TokenKind.TYPE, word, "", start_line, start_col))
            else:
                tokens.append(Token(TokenKind.IDENT, word, "", start_line, start_col))
            continue

        if ch.isdigit():
            begin = pos
            while pos < n and source[pos].isdigit():
                pos += 1
                col += 1
            if peek() == ".":
                if peek(1).isdigit():
                    pos += 1
                    col += 1
                    while pos < n and source[pos].isdigit():
                        pos += 1
                        col += 1
                else:
                    raise ScriptSyntaxError(
                        "미종결 숫자 리터럴(소수점 뒤 숫자 없음)", line, col
                    )
            tokens.append(
                Token(TokenKind.NUMBER, source[begin:pos], "", start_line, start_col)
            )
            continue

        two = ch + peek(1)
        if two in _TWO_CHAR_OPS:
            tokens.append(Token(TokenKind.OP, two, _TWO_CHAR_OPS[two], start_line, start_col))
            pos += 2
            col += 2
            continue

        if ch in _ONE_CHAR_OPS:
            tokens.append(Token(TokenKind.OP, ch, _ONE_CHAR_OPS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        if ch in _DELIMS:
            tokens.append(Token(TokenKind.DELIM, ch, _DELIMS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        raise ScriptSyntaxError(f"예상치 못한 문자 {ch!r}", start_line, start_col)

    tokens.append(Token(TokenKind.EOF, "", "", line, col))
    return tokens
