"""DSL-3 파서의 표현식 문법 — 토큰 커서 기반 클래스(`_ExprParser`).

`parser.py`가 §2.4(상한 280줄)를 지키기 위해 표현식 문법(or/and/not/cmp/
arith/term/unary/postfix/primary/call/request)과 토큰 커서 원시 연산을
이 파일로 분리했다. `_Parser`(parser.py)가 이 클래스를 상속해 decl 파싱을
얹는다 — 문법표·의미는 그대로, 파일 경계만 나눈다.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import cast

from src.core.script.grammar.ast import (
    BinaryExpr,
    BinaryOp,
    CallExpr,
    Expr,
    Identifier,
    NotExpr,
    NumberLiteral,
    PostfixExpr,
    RequestExpr,
    StringLiteral,
    UnaryExpr,
)
from src.core.script.grammar.lexer import ScriptSyntaxError, Token, TokenKind

_NAMESPACES = frozenset({"ta", "math", "series", "strategy"})
_CMP_OPS = frozenset({"<", "<=", "==", ">=", ">"})
_CROSS_OPS = frozenset({"crosses_above", "crosses_below"})
_ARITH_OPS = frozenset({"+", "-"})
_TERM_OPS = frozenset({"*", "/"})


def _number_value(text: str) -> int | float:
    return float(text) if "." in text else int(text)


class _ExprParser:
    """토큰 커서 원시 연산 + expr := or_expr 문법 사슬(전부 좌결합)."""

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

    # primary := NUMBER | STRING | ident | call | request | "(" expr ")"
    # STRING here yields the `string` constant type (M2-3 step 1, task-7847) --
    # DSL-4 (checker.py) is what actually rejects it from the numeric/bool lattice.
    def _primary(self) -> Expr:
        tok = self._peek()
        if tok.kind is TokenKind.NUMBER:
            self._advance()
            return NumberLiteral(value=_number_value(tok.value))
        if tok.kind is TokenKind.STRING:
            self._advance()
            return StringLiteral(value=tok.value)
        if tok.kind is TokenKind.KEYWORD and tok.value == "request":
            return self._request_expr()
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
        """call := ns "." ident "(" args ")" — ns ∈ {ta, math, series, strategy}."""
        ns_tok = self._advance()
        if ns_tok.value not in _NAMESPACES:
            raise ScriptSyntaxError(
                f"알 수 없는 네임스페이스 {ns_tok.value!r}(ta/math/series/strategy만 허용)",
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

    def _request_expr(self) -> RequestExpr:
        """request "(" STRING "," STRING "," expr ")" — M2-2a: symbol·
        timeframe은 컴파일 시 상수(문자열 리터럴)여야 한다(동적 심볼 금지).
        `_expect(TokenKind.STRING, ...)`가 리터럴이 아닌 인자(식별자·연산식
        등)를 여기서 즉시 `SCRIPT_SYNTAX`로 거부한다 — DSL-4 타입 체커까지
        기다리지 않는다."""
        self._advance()  # "request"
        self._expect(TokenKind.DELIM, "(", "request 뒤에는 '('가 필요합니다")
        symbol = self._expect(
            TokenKind.STRING,
            None,
            "request()의 symbol 인자는 문자열 리터럴이어야 합니다(동적 심볼 금지)",
        ).value
        self._expect(TokenKind.DELIM, ",", "request의 symbol 인자 뒤에는 ','가 필요합니다")
        timeframe = self._expect(
            TokenKind.STRING,
            None,
            "request()의 timeframe 인자는 문자열 리터럴이어야 합니다(동적 심볼 금지)",
        ).value
        self._expect(TokenKind.DELIM, ",", "request의 timeframe 인자 뒤에는 ','가 필요합니다")
        expr = self._expr()
        self._expect(TokenKind.DELIM, ")", "request 인자 뒤에는 ')'가 필요합니다")
        return RequestExpr(symbol=symbol, timeframe=timeframe, expr=expr)
