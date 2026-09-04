"""DSL-3 — AIOS Script 재귀하향 파서.

Spec: L4_analytics_authoring_backtest_marketplace_v1.0.md §3.3(문법 전
프로덕션), §9.4(DSL-3), §2.4(상한 280줄). DSL-2 `tokenize()` 토큰만
입력으로 받고 DSL-1 `ast.py` 노드만 출력한다(decision: 재구현 금지).
문법표 밖 프로덕션(반복문·`security()`류, ta/math/series 밖 네임스페이스,
변수·음수 postfix 인덱스)은 노드를 만들지 않고 전부 `SCRIPT_SYNTAX`(렉서의
`ScriptSyntaxError` 재사용 — taxonomy를 늘리지 않는다)로 (line, col)과
함께 거부한다. 타입·미래참조·리소스 검사는 DSL-4/5/6 몫이라 선취하지
않는다(`ns.ident()`가 레지스트리에 실재하는지는 검사하지 않는다).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from src.core.script.grammar.ast import (
    BinaryExpr,
    BinaryOp,
    CallExpr,
    Decl,
    Expr,
    Identifier,
    InputDecl,
    LetDecl,
    NotExpr,
    NumberLiteral,
    OrderDecl,
    PlotDecl,
    PostfixExpr,
    Program,
    SignalDecl,
    TypeName,
    TypeNode,
    UnaryExpr,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, Token, TokenKind, tokenize

_NAMESPACES = frozenset({"ta", "math", "series"})
_CMP_OPS = frozenset({"<", "<=", "==", ">=", ">"})
_CROSS_OPS = frozenset({"crosses_above", "crosses_below"})
_ARITH_OPS = frozenset({"+", "-"})
_TERM_OPS = frozenset({"*", "/"})


def parse(source: str) -> Program:
    """AIOS Script 소스 전체를 `Program`으로 파싱한다(`program := decl*`)."""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    decls: list[Decl] = []
    while parser._peek().kind is not TokenKind.EOF:
        decls.append(parser._decl())
    return Program(decls=tuple(decls))


def _number_value(text: str) -> int | float:
    return float(text) if "." in text else int(text)


class _Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

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
            raise ScriptSyntaxError(message, tok.line, tok.col)
        return self._advance()

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

    # expr := or_expr; or/and/cmp/arith/term은 전부 좌결합
    def _expr(self) -> Expr:
        return self._binary_keyword(self._and_expr, "or", "or")

    def _and_expr(self) -> Expr:
        return self._binary_keyword(self._not_expr, "and", "and")

    def _binary_keyword(self, operand: Callable[[], Expr], keyword: str, op: BinaryOp) -> Expr:
        left = operand()
        while self._check(TokenKind.KEYWORD, keyword):
            self._advance()
            left = BinaryExpr(op=op, left=left, right=operand())
        return left

    def _not_expr(self) -> Expr:
        if self._check(TokenKind.KEYWORD, "not"):
            self._advance()
            return NotExpr(operand=self._not_expr())
        return self._cmp()

    def _cmp(self) -> Expr:
        left = self._arith()
        tok = self._peek()
        if tok.kind is TokenKind.OP and tok.value in _CMP_OPS:
            self._advance()
            return BinaryExpr(op=cast(BinaryOp, tok.value), left=left, right=self._arith())
        if tok.kind is TokenKind.KEYWORD and tok.value in _CROSS_OPS:
            self._advance()
            return BinaryExpr(op=cast(BinaryOp, tok.value), left=left, right=self._arith())
        return left

    def _arith(self) -> Expr:
        return self._binary_op(self._term, _ARITH_OPS)

    def _term(self) -> Expr:
        return self._binary_op(self._unary, _TERM_OPS)

    def _binary_op(self, operand: Callable[[], Expr], ops: frozenset[str]) -> Expr:
        left = operand()
        while self._peek().kind is TokenKind.OP and self._peek().value in ops:
            op = cast(BinaryOp, self._advance().value)
            left = BinaryExpr(op=op, left=left, right=operand())
        return left

    def _unary(self) -> Expr:
        if self._peek().kind is TokenKind.OP and self._peek().value == "-":
            self._advance()
            return UnaryExpr(op="-", operand=self._unary())
        return self._postfix()

    # postfix := primary ("[" INT "]")? — 과거참조만(상수 n>=0)
    def _postfix(self) -> Expr:
        base = self._primary()
        if not self._check(TokenKind.DELIM, "["):
            return base
        self._advance()
        idx_tok = self._peek()
        if idx_tok.kind is not TokenKind.NUMBER or "." in idx_tok.value:
            raise ScriptSyntaxError(
                "postfix 인덱스는 0 이상 정수 상수만 허용합니다(음수·변수 인덱스 금지)",
                idx_tok.line,
                idx_tok.col,
            )
        self._advance()
        self._expect(TokenKind.DELIM, "]", "postfix 인덱스 뒤에는 ']'가 필요합니다")
        return PostfixExpr(base=base, index=int(idx_tok.value))

    # primary := NUMBER | ident | call | "(" expr ")"
    def _primary(self) -> Expr:
        tok = self._peek()
        if tok.kind is TokenKind.NUMBER:
            self._advance()
            return NumberLiteral(value=_number_value(tok.value))
        if tok.kind is TokenKind.IDENT:
            if self._peek(1).kind is TokenKind.DELIM and self._peek(1).value == ".":
                return self._call()
            self._advance()
            return Identifier(name=tok.value)
        if tok.kind is TokenKind.DELIM and tok.value == "(":
            self._advance()
            expr = self._expr()
            self._expect(TokenKind.DELIM, ")", "'(' 뒤 표현식은 ')'로 닫아야 합니다")
            return expr
        raise ScriptSyntaxError(f"예상치 못한 토큰 {tok.value!r}", tok.line, tok.col)

    def _call(self) -> CallExpr:
        """call := ns "." ident "(" args ")" — ns ∈ {ta, math, series}."""
        ns_tok = self._advance()
        if ns_tok.value not in _NAMESPACES:
            raise ScriptSyntaxError(
                f"알 수 없는 네임스페이스 {ns_tok.value!r}(ta/math/series만 허용)",
                ns_tok.line,
                ns_tok.col,
            )
        self._expect(TokenKind.DELIM, ".", "네임스페이스 뒤에는 '.'가 필요합니다")
        ident = self._expect(
            TokenKind.IDENT, None, "네임스페이스 뒤에는 함수 식별자가 필요합니다"
        ).value
        self._expect(TokenKind.DELIM, "(", "함수 이름 뒤에는 '('가 필요합니다")
        args: list[Expr] = []
        if not self._check(TokenKind.DELIM, ")"):
            args.append(self._expr())
            while self._check(TokenKind.DELIM, ","):
                self._advance()
                args.append(self._expr())
        self._expect(TokenKind.DELIM, ")", "호출 인자 뒤에는 ')'가 필요합니다")
        return CallExpr(ns=ns_tok.value, ident=ident, args=tuple(args))
