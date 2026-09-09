"""DSL-14 — Pine Script v5 partial-grammar lexer.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
DSL-14 ("script/import/pine/{lexer,parser}.py — Pine Script v5 partial
grammar").

Only tokenizes — parsing and rejection decisions are `parser.py`'s job.
`tokenize()` is a pure function: it takes one source string and either
returns a `Token` list or raises `PineSyntaxError` — no I/O, global state,
randomness, or clock reads.

Pine has no semicolons; a newline is the statement boundary (the key
difference from AIOS Script). Paren/bracket depth is counted, and while
depth>0 a newline is skipped like whitespace (allowing multi-line call
argument layout); `NEWLINE` tokens are only emitted at depth==0 — Pine's
indentation-based blocks (if/for) are out of scope for this leaf (the parser
itself rejects those keywords), so indentation rules are not implemented
either.

Unverified: this parser follows only the commonly known lexical rules
(identifiers, numbers, strings, operators) from the public Pine v5 language
reference, not TradingView's private grammar spec — it does not guarantee a
100% match with the original grammar's EBNF.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

# Only reserved words the code must explicitly check start/end points for
# are KEYWORDs. ta/input/strategy (namespaces) are ordinary idents
# grammar-wise and the parser resolves them as "ns '.' ident", so they are
# not treated as reserved words here.
KEYWORDS = frozenset(
    {
        "and",
        "or",
        "not",
        "true",
        "false",
        "var",
        "varip",
        "if",
        "for",
        "while",
        "switch",
    }
)

_TWO_CHAR_OPS = {
    "<=": "LE",
    ">=": "GE",
    "==": "EQEQ",
    "!=": "NEQ",
    ":=": "REASSIGN",
    "=>": "ARROW",
}
_ONE_CHAR_OPS = {
    "<": "LT",
    ">": "GT",
    "+": "PLUS",
    "-": "MINUS",
    "*": "STAR",
    "/": "SLASH",
    "%": "PERCENT",
    "=": "ASSIGN",
}
_DELIMS = {
    "(": "LPAREN",
    ")": "RPAREN",
    "[": "LBRACKET",
    "]": "RBRACKET",
    ",": "COMMA",
    ".": "DOT",
}


class TokenKind(enum.Enum):
    KEYWORD = "KEYWORD"
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    OP = "OP"
    DELIM = "DELIM"
    NEWLINE = "NEWLINE"
    EOF = "EOF"


@dataclass(frozen=True, slots=True)
class Token:
    """`value` is the raw lexeme as written — for STRING, `value` has the
    opening/closing quotes stripped (escapes already resolved); for NUMBER it
    is the raw text before conversion (int/float promotion is the parser's
    job). `subtype` is populated only for OP/DELIM, so the parser can branch
    without string comparisons."""

    kind: TokenKind
    value: str
    subtype: str
    line: int
    col: int


class PineSyntaxError(Exception):
    """Raised when Pine source cannot be tokenized/parsed -- always carries
    (line, col) so the caller can point at the error location. This leaf does
    not split out-of-grammar syntax (unsupported) from genuine lexical/syntax
    errors into separate taxonomy (both reach the same conclusion, "this Pine
    code cannot be imported," so callers do not need to distinguish them --
    decision, following the precedent of AIOS Script DSL-3's
    `ScriptSyntaxError` single-code reuse)."""

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
    """Converts Pine source into a token list. The last token is always
    `TokenKind.EOF` (so the parser can detect the end during lookahead with
    no special case)."""
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    n = len(source)
    depth = 0  # ()/[] nesting depth -- above 0, newlines are ignored like whitespace.

    def peek(offset: int = 0) -> str:
        idx = pos + offset
        return source[idx] if idx < n else ""

    while pos < n:
        ch = source[pos]

        if ch == "\r" or ch == "\n":
            advance = 2 if ch == "\r" and peek(1) == "\n" else 1
            pos += advance
            line += 1
            col = 1
            if depth == 0 and tokens and tokens[-1].kind is not TokenKind.NEWLINE:
                tokens.append(Token(TokenKind.NEWLINE, "\n", "", line - 1, col))
            continue
        if ch in (" ", "\t"):
            pos += 1
            col += 1
            continue
        if ch == "/" and peek(1) == "/":
            while pos < n and source[pos] not in ("\n", "\r"):
                pos += 1
                col += 1
            continue

        start_line, start_col = line, col

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

        if ch in ("'", '"'):
            quote = ch
            pos += 1
            col += 1
            chars: list[str] = []
            while True:
                if pos >= n or source[pos] in ("\n", "\r"):
                    raise PineSyntaxError("미종결 문자열 리터럴", start_line, start_col)
                cur = source[pos]
                if cur == quote:
                    pos += 1
                    col += 1
                    break
                if cur == "\\" and peek(1) in (quote, "\\"):
                    chars.append(peek(1))
                    pos += 2
                    col += 2
                    continue
                chars.append(cur)
                pos += 1
                col += 1
            tokens.append(
                Token(TokenKind.STRING, "".join(chars), "", start_line, start_col)
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
            if ch in ("(", "["):
                depth += 1
            elif ch in (")", "]"):
                depth = max(0, depth - 1)
            tokens.append(Token(TokenKind.DELIM, ch, _DELIMS[ch], start_line, start_col))
            pos += 1
            col += 1
            continue

        raise PineSyntaxError(f"예상치 못한 문자 {ch!r}", start_line, start_col)

    tokens.append(Token(TokenKind.EOF, "", "", line, col))
    return tokens
