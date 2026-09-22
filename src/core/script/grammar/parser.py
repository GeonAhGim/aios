"""DSL-3 — AIOS Script 재귀하향 파서.

Spec: L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3(문법 전
프로덕션), §9.4(DSL-3), §2.4(상한 280줄). DSL-2 `tokenize()` 토큰만
입력으로 받고 DSL-1 `ast.py` 노드만 출력한다(decision: 재구현 금지).
문법표 밖 프로덕션(반복문·`security()`류, ta/math/series 밖 네임스페이스,
변수·음수 postfix 인덱스)은 노드를 만들지 않고 전부 `SCRIPT_SYNTAX`(렉서의
`ScriptSyntaxError` 재사용 — taxonomy를 늘리지 않는다)로 (line, col)과
함께 거부한다. 타입·미래참조·리소스 검사는 DSL-4/5/6 몫이라 선취하지
않는다(`ns.ident()`가 레지스트리에 실재하는지는 검사하지 않는다).
토큰 커서 원시 연산과 표현식 문법(or/and/not/cmp/arith/term/unary/
postfix/primary/call/request)은 §2.4 상한을 지키기 위해 `parser_expr.py`
의 `_ExprParser`로 분리했다 — 이 클래스는 그 위에 decl 파싱만 얹는다.
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
    """AIOS Script 소스 전체를 `Program`으로 파싱한다(`program := decl*`)."""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    decls: list[Decl] = []
    while parser._peek().kind is not TokenKind.EOF:
        decls.append(parser._decl())
    return Program(decls=tuple(decls))


class _Parser(_ExprParser):
    """decl := input | let | plot | signal | order — 표현식 문법은 상위 클래스."""

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

    # type := "int" | "float" | "bool" | "series<float>" | "series<bool>"
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
        raise ScriptSyntaxError(
            "타입이 필요합니다(int/float/bool/series<float>/series<bool>)", tok.line, tok.col
        )
