"""DSL-3 — AIOS Script recursive-descent parser.

Spec: L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3 (pre-production
grammar), §9.4 (DSL-3), §2.4 (280-line cap). Takes only DSL-2
`tokenize()` tokens as input and outputs only DSL-1 `ast.py` nodes
(decision: no re-implementation). Productions outside the grammar spec
(loops, `security()`-like constructs, namespaces outside ta/math/series/strategy,
variables, negative postfix indices) produce no nodes; all are rejected as
`SCRIPT_SYNTAX` (reusing the lexer's `ScriptSyntaxError` — does not expand the
taxonomy) together with (line, col). Type, forward-reference, and resource
checks belong to DSL-4/5/6, so this parser does not preempt them
(`ns.ident()` does not verify whether the identifier actually exists in the
registry). Token-cursor primitive ops and expression grammar (or/and/not/cmp/
arith/term/unary/postfix/primary/call/request) are split into `_ExprParser` in
`parser_expr.py` to respect the §2.4 line cap — this class layers only decl
parsing on top of that.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

from src.core.script.grammar.ast import (
    Decl,
    Expr,
    InputDecl,
    LetDecl,
    OrderDecl,
    PlotDecl,
    Program,
    SignalDecl,
    TypeName,
    TypeNode,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, TokenKind, tokenize
from src.core.script.grammar.parser_expr import _ExprParser, _number_value


def parse(source: str) -> Program:
    """Parse the entire AIOS Script source into a `Program` (`program := decl*`)."""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    decls: list[Decl] = []
    while parser._peek().kind is not TokenKind.EOF:
        decls.append(parser._decl())
    return Program(decls=tuple(decls))


class _Parser(_ExprParser):
    """decl := input | let | plot | signal | order — expression grammar is in the parent class."""

    # decl := input | let | plot | signal | order
    def _decl(self) -> Decl:
        tok = self._peek()
        if tok.kind is TokenKind.KEYWORD:
            handlers: dict[str, Callable[[], Decl]] = {
                "input": self._input_decl,
                "let": self._let_decl,
                "plot": self._plot_decl,
                "signal": self._signal_decl,
                "order": self._order_decl,
            }
            handler = handlers.get(tok.value)
            if handler is not None:
                return handler()
        raise ScriptSyntaxError(
            f"decl은 input/let/plot/signal/order로 시작해야 합니다(받음: {tok.value!r})",
            tok.line,
            tok.col,
        )

    def _input_decl(self) -> InputDecl:
        self._advance()  # "input"
        name = self._expect(TokenKind.IDENT, None, "input 뒤에는 식별자가 필요합니다").value
        self._expect(TokenKind.DELIM, ":", "input 이름 뒤에는 ':'가 필요합니다")
        type_node = self._type()
        self._expect(TokenKind.DELIM, "=", "input 타입 뒤에는 '='가 필요합니다")
        value_tok = self._expect(TokenKind.NUMBER, None, "input 값은 숫자 리터럴이어야 합니다")
        return InputDecl(name=name, type=type_node, value=_number_value(value_tok.value))

    def _let_decl(self) -> LetDecl:
        self._advance()  # "let"
        name = self._expect(TokenKind.IDENT, None, "let 뒤에는 식별자가 필요합니다").value
        self._expect(TokenKind.DELIM, "=", "let 이름 뒤에는 '='가 필요합니다")
        return LetDecl(name=name, expr=self._expr())

    def _plot_decl(self) -> PlotDecl:
        self._advance()  # "plot"
        self._expect(TokenKind.DELIM, "(", "plot 뒤에는 '('가 필요합니다")
        expr = self._expr()
        style: Expr | None = None
        if self._check(TokenKind.DELIM, ","):
            self._advance()
            style = self._expr()
        self._expect(TokenKind.DELIM, ")", "plot 인자 뒤에는 ')'가 필요합니다")
        return PlotDecl(expr=expr, style=style)

    def _signal_decl(self) -> SignalDecl:
        self._advance()  # "signal"
        name = self._expect(TokenKind.IDENT, None, "signal 뒤에는 식별자가 필요합니다").value
        self._expect(TokenKind.DELIM, "=", "signal 이름 뒤에는 '='가 필요합니다")
        return SignalDecl(name=name, expr=self._expr())

    def _order_decl(self) -> OrderDecl:
        self._advance()  # "order"
        self._expect(TokenKind.DELIM, "(", "order 뒤에는 '('가 필요합니다")
        side = self._expr()
        self._expect(TokenKind.DELIM, ",", "order side 뒤에는 ','가 필요합니다")
        qty_expr = self._expr()
        opts: Expr | None = None
        if self._check(TokenKind.DELIM, ","):
            self._advance()
            opts = self._expr()
        self._expect(TokenKind.DELIM, ")", "order 인자 뒤에는 ')'가 필요합니다")
        self._expect(TokenKind.KEYWORD, "when", "order(...) 뒤에는 'when'이 필요합니다")
        return OrderDecl(side=side, qty_expr=qty_expr, opts=opts, when=self._expr())

    # type := "int" | "float" | "bool" | "series<float>" | "series<bool>" | "array<float>"
    def _type(self) -> TypeNode:
        tok = self._peek()
        if tok.kind is TokenKind.TYPE:
            self._advance()
            return TypeNode(name=cast(TypeName, tok.value))
        if tok.kind is TokenKind.IDENT and tok.value == "series":
            self._advance()
            self._expect(TokenKind.OP, "<", "series 뒤에는 '<'가 필요합니다")
            inner = self._peek()
            if inner.kind is TokenKind.TYPE and inner.value in ("float", "bool"):
                self._advance()
                self._expect(TokenKind.OP, ">", "series<...> 뒤에는 '>'가 필요합니다")
                return TypeNode(name=cast(TypeName, f"series<{inner.value}>"))
            raise ScriptSyntaxError(
                "series<...>의 내부 타입은 float 또는 bool이어야 합니다", inner.line, inner.col
            )
        if tok.kind is TokenKind.IDENT and tok.value == "array":
            self._advance()
            self._expect(TokenKind.OP, "<", "array 뒤에는 '<'가 필요합니다")
            inner = self._peek()
            if inner.kind is TokenKind.TYPE and inner.value == "float":
                self._advance()
                self._expect(TokenKind.OP, ">", "array<...> 뒤에는 '>'가 필요합니다")
                return TypeNode(name="array<float>")
            raise ScriptSyntaxError(
                "array<...>의 내부 타입은 float만 허용됩니다", inner.line, inner.col
            )
        raise ScriptSyntaxError(
            "타입이 필요합니다(int/float/bool/series<float>/series<bool>/array<float>)",
            tok.line,
            tok.col,
        )
