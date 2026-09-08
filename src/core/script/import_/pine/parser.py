"""DSL-14 — Pine Script v5 subset recursive-descent parser.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9.9(DSL-14). Consumes tokens from the sibling Pine lexer (never the AIOS
Script grammar — different language, different AST) and produces a frozen
Pine AST. Supported surface is exactly `SUPPORTED_CONSTRUCTS`; anything
else is rejected with `UnsupportedPineConstruct` carrying 1-based
(line, column) of the offending construct. Transpile to AIOS Script is
DSL-15, out of scope here. Pure: no I/O, no globals beyond constants.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import TypeAlias

from src.core.script.import_.pine.lexer import (
    PineSyntaxError,
    Token,
    TokenKind,
    tokenize,
)

# Single source of truth for the supported subset (DoD (a)). Tests iterate
# this tuple and assert each sample parses; a table that lives only in
# docs/comments does not count.
SUPPORTED_CONSTRUCTS: tuple[tuple[str, str], ...] = (
    ("version_directive", '//@version=5\nindicator("x")\n'),
    ("indicator_decl", 'indicator("x")\n'),
    ("strategy_decl", 'strategy("x")\n'),
    ("input_call", 'len = input.int(14, "Length")\n'),
    ("input_typed", "length = input(14)\n"),
    ("var_decl_assign", "x = close\n"),
    ("var_decl_reassign", "x := x + 1\n"),
    ("series_arith", "y = (open + high - low * close) / 2\n"),
    ("series_compare", "c = close >= open\n"),
    ("series_logic", "ok = close > open and not (low < open) or false\n"),
    ("ta_call", "fast = ta.sma(close, 14)\n"),
    ("ta_call_named_args", "slow = ta.ema(source=close, length=21)\n"),
    ("plot", 'plot(close, "C")\n'),
    ("plot_named_args", 'plot(series=fast, title="Fast")\n'),
    ("history_ref", "prev = close[1]\n"),
    ("strategy_entry", 'strategy.entry("L", strategy.long)\n'),
    ("strategy_exit", 'strategy.exit("X", from_entry="L")\n'),
)

_UNSUPPORTED_NAMESPACES: dict[str, str] = {
    "request": "namespace request.* (e.g. request.security())",
    "array": "namespace array.*",
    "matrix": "namespace matrix.*",
    "map": "namespace map.*",
    "table": "namespace table.*",
    "line": "drawing line.*",
    "label": "drawing label.*",
    "box": "drawing box.*",
    "polyline": "drawing polyline.*",
    "linefill": "drawing linefill.*",
    "alert": "alert.*",
    "barstate": "barstate.*",
    "syminfo": "syminfo.*",
    "timeframe": "timeframe.*",
    "ticker": "ticker.*",
    "math": "math.* namespace (not part of the DSL-14 subset)",
    "str": "str.* namespace (not part of the DSL-14 subset)",
    "color": "color.* namespace (not part of the DSL-14 subset)",
}
_UNSUPPORTED_KEYWORDS: dict[str, str] = {
    "if": "if statement/expression blocks",
    "for": "for loops",
}
_TOP_LEVEL_FUNCTIONS = frozenset({"indicator", "strategy", "plot"})
_INPUT_FUNCTIONS = frozenset({"int", "float", "bool", "string", "source", "time"})
_BUILTIN_SERIES = frozenset({"open", "high", "low", "close", "volume", "hl2", "hlc3", "ohlc4"})
_TA_BARE_IDENT = frozenset({"na", "bar_index", "dayofmonth"})
_BUILTIN_CONSTANTS = frozenset({"barstate", "session", "timeframe", "syminfo", "strategy"})


class UnsupportedPineConstruct(Exception):
    """The source uses Pine v5 syntax outside the DSL-14 subset.
    ``line``/``column`` are 1-based and point at the construct start."""

    def __init__(self, construct: str, line: int, column: int) -> None:
        super().__init__(
            f"unsupported Pine construct: {construct} (line {line}, column {column})"
        )
        self.construct = construct
        self.line = line
        self.column = column


class PineNodeKind(enum.Enum):
    PROGRAM = "program"
    VERSION = "version"
    DECLARATION = "declaration"
    INPUT = "input"
    CALL = "call"
    PLOT = "plot"
    STRATEGY_ACTION = "strategy_action"
    ASSIGNMENT = "assignment"
    NUMBER = "number"
    STRING = "string"
    BOOL = "bool"
    IDENT = "ident"
    UNARY = "unary"
    BINARY = "binary"
    HISTORY_REF = "history_ref"


@dataclass(frozen=True, slots=True)
class PineNode:
    """Immutable AST node. ``kind`` discriminates; ``fields`` maps child
    name -> node | tuple of nodes | int | float | str | bool. Only these
    shapes exist, so `serialize` is total."""

    kind: PineNodeKind
    line: int
    col: int
    fields: tuple[tuple[str, object], ...] = ()


ChildValue: TypeAlias = "PineNode | tuple[PineNode, ...] | int | float | str | bool"


def serialize(node: PineNode) -> object:
    """Deterministic JSON-compatible tree: same source -> same bytes when
    dumped with ``json.dumps(..., sort_keys=True, ensure_ascii=True)``."""
    data: dict[str, object] = {"kind": node.kind.value, "line": node.line, "col": node.col}
    for name, value in node.fields:
        if isinstance(value, PineNode):
            data[name] = serialize(value)
        elif isinstance(value, tuple):
            data[name] = [serialize(item) for item in value]
        else:
            data[name] = value
    return data


def _number_value(text: str) -> int | float:
    return float(text) if "." in text else int(text)


def parse(source: str) -> PineNode:
    """Pine v5 source -> `PineNode` program. Lexical errors surface as
    `PineSyntaxError`; supported-subset violations as
    `UnsupportedPineConstruct` (both carry line/column)."""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    return parser._program()


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    # ---- token helpers -------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = self._pos + offset
        if idx >= len(self._tokens):
            return self._tokens[-1]  # EOF
        return self._tokens[idx]

    def _advance(self) -> Token:
        tok = self._peek()
        if self._pos < len(self._tokens) - 1:
            self._pos += 1
        return tok

    def _check(self, kind: TokenKind, value: str | None = None) -> bool:
        tok = self._peek()
        return tok.kind is kind and (value is None or tok.value == value)

    def _expect(self, kind: TokenKind, value: str | None, message: str) -> Token:
        if not self._check(kind, value):
            tok = self._peek()
            raise PineSyntaxError(message, tok.line, tok.col)
        return self._advance()

    # ---- program := statement* -----------------------------------------

    def _program(self) -> PineNode:
        statements: list[PineNode] = []
        while not self._check(TokenKind.EOF):
            statements.append(self._statement())
        return PineNode(PineNodeKind.PROGRAM, 1, 1, (("statements", tuple(statements)),))

    def _statement(self) -> PineNode:
        tok = self._peek()
        if tok.kind is TokenKind.OP and tok.value == "=>":
            raise UnsupportedPineConstruct(
                "user-defined function (=>)", tok.line, tok.col
            )
        if tok.kind is TokenKind.KEYWORD and tok.value in _UNSUPPORTED_KEYWORDS:
            raise UnsupportedPineConstruct(
                _UNSUPPORTED_KEYWORDS[tok.value], tok.line, tok.col
            )
        if tok.kind is TokenKind.NUMBER and self._peek(1).value == ".":
            return self._version_directive()
        if tok.kind is not TokenKind.IDENT:
            raise PineSyntaxError(f"unexpected token {tok.value!r}", tok.line, tok.col)
        return self._ident_statement(tok)

    def _version_directive(self) -> PineNode:
        major = self._advance()
        self._expect(TokenKind.DELIM, ".", "'//@version' needs a minor version")
        minor = self._expect(TokenKind.NUMBER, None, "'//@version' needs a minor version")
        self._expect(TokenKind.DELIM, "=", "'//@version=N' needs '=' before the tag")
        tag = self._expect(TokenKind.IDENT, None, "'//@version=N' needs a tag")
        if tag.value != "version":
            raise PineSyntaxError(
                f"unknown directive tag {tag.value!r} (expected 'version')",
                tag.line,
                tag.col,
            )
        value = int(f"{major.value}{minor.value}")
        if value != 5:
            raise UnsupportedPineConstruct(
                f"Pine version {value} (only v5 is supported)", major.line, major.col
            )
        return PineNode(PineNodeKind.VERSION, major.line, major.col, (("version", value),))

    def _ident_statement(self, first: Token) -> PineNode:
        if first.value in _UNSUPPORTED_NAMESPACES:
            raise UnsupportedPineConstruct(
                _UNSUPPORTED_NAMESPACES[first.value], first.line, first.col
            )
        if first.value in _TOP_LEVEL_FUNCTIONS and self._peek(1).value == "(":
            return self._top_level_call(first)
        name, dotted = self._dotted_path(first)
        tok = self._peek()
        if dotted and tok.value == "(" and name[0] == "input":
            return self._input_call(first, name)
        if tok.value in ("=", ":="):
            if dotted:
                raise PineSyntaxError(
                    f"cannot assign to a dotted name {'.'.join(name)!r}",
                    first.line,
                    first.col,
                )
            self._advance()
            expr = self._expr()
            if self._peek().value == "=>":
                raise UnsupportedPineConstruct(
                    "user-defined function (=>)", self._peek().line, self._peek().col
                )
            return PineNode(
                PineNodeKind.ASSIGNMENT,
                first.line,
                first.col,
                (("target", name[0]), ("reassign", tok.value == ":="), ("value", expr)),
            )
        if dotted and tok.value == "(":
            raise UnsupportedPineConstruct(
                f"call {name[0]}.{name[1]}() (outside the DSL-14 subset)",
                first.line,
                first.col,
            )
        if tok.value == "(":
            raise PineSyntaxError(
                f"unknown top-level call {first.value!r}", first.line, first.col
            )
        raise PineSyntaxError(f"unexpected token {tok.value!r}", tok.line, tok.col)

    def _dotted_path(self, first: Token) -> tuple[tuple[str, ...], bool]:
        names = [first.value]
        while self._peek().value == "." and self._peek(1).kind is TokenKind.IDENT:
            self._advance()
            names.append(self._advance().value)
        if self._peek().value == ".":
            tok = self._peek()
            raise PineSyntaxError(
                f"expected an identifier after '.' (got {tok.value!r})", tok.line, tok.col
            )
        return tuple(names), len(names) > 1

    # ---- declarations / statements -------------------------------------

    def _top_level_call(self, name_tok: Token) -> PineNode:
        self._expect(TokenKind.DELIM, "(", "expected '(' after the declaration name")
        args, kwargs = self._args()
        fields: tuple[tuple[str, object], ...]
        if name_tok.value == "plot":
            fields = (("args", tuple(args)), ("kwargs", kwargs))
            return PineNode(PineNodeKind.PLOT, name_tok.line, name_tok.col, fields)
        fields = (("name", name_tok.value), ("args", tuple(args)), ("kwargs", kwargs))
        return PineNode(PineNodeKind.DECLARATION, name_tok.line, name_tok.col, fields)

    def _input_call(self, start: Token, name: tuple[str, ...]) -> PineNode:
        if len(name) != 2 or name[1] not in _INPUT_FUNCTIONS:
            fn = ".".join(name)
            raise UnsupportedPineConstruct(
                f"input function {fn}() (input.int/float/bool/string/source/time only)",
                start.line,
                start.col,
            )
        self._expect(TokenKind.DELIM, "(", "expected '(' after the input function")
        args, kwargs = self._args()
        return PineNode(
            PineNodeKind.INPUT,
            start.line,
            start.col,
            (("fn", name[1]), ("args", tuple(args)), ("kwargs", kwargs)),
        )

    def _strategy_call(self, start: Token, name: tuple[str, ...]) -> PineNode:
        if len(name) != 2 or name[1] not in ("entry", "exit"):
            fn = ".".join(name)
            raise UnsupportedPineConstruct(
                f"strategy.{fn}() (strategy.entry/strategy.exit only)",
                start.line,
                start.col,
            )
        self._expect(TokenKind.DELIM, "(", "expected '(' after the strategy function")
        args, kwargs = self._args()
        return PineNode(
            PineNodeKind.STRATEGY_ACTION,
            start.line,
            start.col,
            (("action", name[1]), ("args", tuple(args)), ("kwargs", kwargs)),
        )

    def _args(self) -> tuple[list[PineNode], tuple[tuple[str, PineNode], ...]]:
        args: list[PineNode] = []
        kwargs: list[tuple[str, PineNode]] = []
        if self._check(TokenKind.DELIM, ")"):
            self._advance()
            return args, ()
        while True:
            if self._peek().kind is TokenKind.IDENT and self._peek(1).value == "=":
                key_tok = self._advance()
                self._advance()
                kwargs.append((key_tok.value, self._expr()))
            else:
                if kwargs:
                    tok = self._peek()
                    raise PineSyntaxError(
                        "positional arguments must precede keyword arguments",
                        tok.line,
                        tok.col,
                    )
                args.append(self._expr())
            if self._check(TokenKind.DELIM, ","):
                self._advance()
                continue
            self._expect(TokenKind.DELIM, ")", "expected ',' or ')' after an argument")
            return args, tuple(kwargs)

    # ---- expressions ----------------------------------------------------

    def _expr(self) -> PineNode:
        return self._binary_keyword(self._and_expr, "or")

    def _and_expr(self) -> PineNode:
        return self._binary_keyword(self._not_expr, "and")

    def _binary_keyword(self, operand: object, keyword: str) -> PineNode:
        parse_operand = operand  # narrowing helper for mypy
        assert callable(parse_operand)
        left: PineNode = parse_operand()
        while self._check(TokenKind.KEYWORD, keyword):
            op_tok = self._advance()
            right: PineNode = parse_operand()
            left = PineNode(
                PineNodeKind.BINARY,
                op_tok.line,
                op_tok.col,
                (("op", keyword), ("left", left), ("right", right)),
            )
        return left

    def _not_expr(self) -> PineNode:
        if self._check(TokenKind.KEYWORD, "not"):
            tok = self._advance()
            return PineNode(
                PineNodeKind.UNARY, tok.line, tok.col,
                (("op", "not"), ("operand", self._not_expr())),
            )
        return self._cmp()

    _CMP_OPS = frozenset({"<", "<=", "==", ">=", ">", "!="})
    _ARITH_OPS = frozenset({"+", "-"})
    _TERM_OPS = frozenset({"*", "/"})

    def _cmp(self) -> PineNode:
        left = self._arith()
        tok = self._peek()
        if tok.kind is TokenKind.OP and tok.value in self._CMP_OPS:
            self._advance()
            return PineNode(
                PineNodeKind.BINARY, tok.line, tok.col,
                (("op", tok.value), ("left", left), ("right", self._arith())),
            )
        return left

    def _arith(self) -> PineNode:
        return self._binary_op(self._term, self._ARITH_OPS)

    def _term(self) -> PineNode:
        return self._binary_op(self._unary, self._TERM_OPS)

    def _binary_op(self, operand: object, ops: frozenset[str]) -> PineNode:
        parse_operand = operand
        assert callable(parse_operand)
        left: PineNode = parse_operand()
        while self._peek().kind is TokenKind.OP and self._peek().value in ops:
            op_tok = self._advance()
            right: PineNode = parse_operand()
            left = PineNode(
                PineNodeKind.BINARY,
                op_tok.line,
                op_tok.col,
                (("op", op_tok.value), ("left", left), ("right", right)),
            )
        return left

    def _unary(self) -> PineNode:
        tok = self._peek()
        if tok.kind is TokenKind.OP and tok.value == "-":
            self._advance()
            return PineNode(
                PineNodeKind.UNARY, tok.line, tok.col,
                (("op", "-"), ("operand", self._unary())),
            )
        return self._postfix()

    def _postfix(self) -> PineNode:
        base = self._primary()
        if not self._check(TokenKind.DELIM, "["):
            return base
        bracket = self._advance()
        idx_tok = self._peek()
        if idx_tok.kind is not TokenKind.NUMBER or "." in idx_tok.value:
            raise PineSyntaxError(
                "history reference needs a non-negative integer literal offset",
                idx_tok.line,
                idx_tok.col,
            )
        self._advance()
        self._expect(TokenKind.DELIM, "]", "history reference needs a closing ']'")
        return PineNode(
            PineNodeKind.HISTORY_REF,
            bracket.line,
            bracket.col,
            (("base", base), ("offset", int(idx_tok.value))),
        )

    def _primary(self) -> PineNode:
        tok = self._peek()
        if tok.kind is TokenKind.NUMBER:
            self._advance()
            return PineNode(
                PineNodeKind.NUMBER, tok.line, tok.col, (("value", _number_value(tok.value)),)
            )
        if tok.kind is TokenKind.STRING:
            self._advance()
            return PineNode(PineNodeKind.STRING, tok.line, tok.col, (("value", tok.value),))
        if tok.kind is TokenKind.KEYWORD and tok.value in ("true", "false"):
            self._advance()
            return PineNode(
                PineNodeKind.BOOL, tok.line, tok.col, (("value", tok.value == "true"),)
            )
        if tok.kind is TokenKind.KEYWORD and tok.value in _UNSUPPORTED_KEYWORDS:
            raise UnsupportedPineConstruct(
                _UNSUPPORTED_KEYWORDS[tok.value], tok.line, tok.col
            )
        if tok.kind is TokenKind.IDENT:
            return self._ident_expr(tok)
        if tok.kind is TokenKind.DELIM and tok.value == "(":
            self._advance()
            expr = self._expr()
            self._expect(TokenKind.DELIM, ")", "parenthesised expression needs a ')'")
            return expr
        raise PineSyntaxError(f"unexpected token {tok.value!r}", tok.line, tok.col)

    def _ident_expr(self, first: Token) -> PineNode:
        if first.value in _UNSUPPORTED_NAMESPACES:
            raise UnsupportedPineConstruct(
                _UNSUPPORTED_NAMESPACES[first.value], first.line, first.col
            )
        name, dotted = self._dotted_path(first)
        if self._peek().value == "(":
            if name[0] == "strategy":
                return self._strategy_call(first, name)
            if name[0] == "ta":
                return self._ta_call(first, name)
            if name[0] == "input":
                return self._input_call(first, name)
            raise UnsupportedPineConstruct(
                f"call {name[0]}() (outside the DSL-14 subset)", first.line, first.col
            )
        if dotted and name[0] in _BUILTIN_CONSTANTS and len(name) == 2:
            return PineNode(
                PineNodeKind.IDENT, first.line, first.col, (("name", ".".join(name)),)
            )
        if dotted:
            raise UnsupportedPineConstruct(
                f"dotted reference {'.'.join(name)!r} (outside the DSL-14 subset)",
                first.line,
                first.col,
            )
        return PineNode(PineNodeKind.IDENT, first.line, first.col, (("name", name[0]),))

    def _ta_call(self, start: Token, name: tuple[str, ...]) -> PineNode:
        if len(name) != 2 or name[1] in _TA_BARE_IDENT:
            fn = ".".join(name)
            raise UnsupportedPineConstruct(
                f"ta.{fn}() is not a callable in the DSL-14 subset", start.line, start.col
            )
        self._expect(TokenKind.DELIM, "(", "expected '(' after the ta.* function")
        args, kwargs = self._args()
        return PineNode(
            PineNodeKind.CALL,
            start.line,
            start.col,
            (("ns", "ta"), ("fn", name[1]), ("args", tuple(args)), ("kwargs", kwargs)),
        )
