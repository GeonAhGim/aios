"""DSL-14 — Pine Script v5 subset lexer.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.9(DSL-14). This is a separate grammar from AIOS Script
(`src/core/script/grammar/`) — Pine-specific tokens live here only:
``//`` line comments, string literals, ``:=``/``=>``, and the named-arg
``=`` token. ``tokenize()`` is a pure function: one source string in,
``Token`` list out, or ``PineSyntaxError``. No I/O, no global state, no
clock, no randomness.

Line/column are 1-based on every token. The trailing EOF token carries
the position just past the last character.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

KEYWORDS = frozenset({"and", "or", "not", "true", "false", "if", "for"})

_TWO_CHAR_OPS = {
    "<=": "LE",
    ">=": "GE",
    "==": "EQEQ",
    "!=": "NE",
    "=>": "ARROW",
    ":=": "WALRUS",
}
_ONE_CHAR_OPS = {
    "<": "LT",
    ">": "GT",
    "+": "PLUS",
    "-": "MINUS",
    "*": "STAR",
    "/": "SLASH",
}
_DELIMS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ":": "COLON",
    ".": "DOT",
    "=": "ASSIGN",
}


class TokenKind(enum.Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OP"
    DELIM = "DELIM"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    """``value`` is the raw lexeme; ``subtype`` names OP/DELIM variants so the
    parser can branch without string lookups (empty for other kinds)."""

    kind: TokenKind
    value: str
    subtype: str
    line: int
    col: int


class PineSyntaxError(Exception):
    """Lexical-level failure. ``line``/``col`` point at the offending
    construct — for an unterminated string, at the opening quote, never at
    EOF."""

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
    """Pine v5 source -> token list; the last token is always
    ``TokenKind.EOF`` so the parser needs no end-of-input special case."""
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

        if ch in "\r\n":
            if ch == "\r" and peek(1) == "\n":
                pos += 1
            pos += 1
            line += 1
            col = 1
            continue
        if ch in (" ", "\t"):
            pos += 1
            col += 1
            continue
        if ch == "/" and peek(1) == "/":
            while pos < n and source[pos] not in "\r\n":
                pos += 1
                col += 1
            continue

        start_line, start_col = line, col

        if ch in ("\"", "'"):
            quote = ch
            begin = pos + 1
            scan = begin
            while True:
                if scan >= n or source[scan] in "\r\n":
                    raise PineSyntaxError(
                        "unterminated string literal", start_line, start_col
                    )
                if source[scan] == "\\":
                    if scan + 1 >= n or source[scan + 1] in "\r\n":
                        raise PineSyntaxError(
                            "unterminated string literal", start_line, start_col
                        )
                    scan += 2
                    continue
                if source[scan] == quote:
                    break
                scan += 1
            text = source[begin:scan]
            tokens.append(Token(TokenKind.STRING, text, quote, start_line, start_col))
            col += scan + 1 - pos
            pos = scan + 1
            continue

        if _is_ident_start(ch):
            begin = pos
            while pos < n and _is_ident_cont(source[pos]):
                pos += 1
                col += 1
            word = source[begin:pos]
            kind = TokenKind.KEYWORD if word in KEYWORDS else TokenKind.IDENT
            tokens.append(Token(kind, word, "", start_line, start_col))
            continue

        if ch.isdigit():
            begin = pos
            while pos < n and source[pos].isdigit():
                pos += 1
                col += 1
            if peek() == "." and peek(1).isdigit():
                pos += 1
                col += 1
                while pos < n and source[pos].isdigit():
                    pos += 1
                    col += 1
            tokens.append(Token(TokenKind.NUMBER, source[begin:pos], "", start_line, start_col))
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

        raise PineSyntaxError(f"unexpected character {ch!r}", start_line, start_col)

    tokens.append(Token(TokenKind.EOF, "", "", line, col))
    return tokens
