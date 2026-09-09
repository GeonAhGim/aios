"""DSL-14 — Pine Script v5 partial-grammar recursive-descent parser.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 DSL-14. Takes only
`lexer.py`'s `tokenize()` tokens as input and outputs only the AST defined in `ast.py`.
Conversion (transpile) to AIOS Script is DSL-15's job and is not done here.

Supported (allow-list): literals (int/float/string/`true`/`false`), namespace-less identifier
references, series arithmetic (`+-*/%`), comparison (`< <= == != >= >`), logic (`and or not`),
historical reference `expr[N]` (N is a constant integer >= 0), `ta.<ident>(...)`/
`input.<ident>(...)` (any identifier allowed inside the namespace), `strategy.entry/exit(...)`,
exactly one bare function call, `plot(...)`, and a single assignment statement `ident = expr`
(Pine has no `let`, so this substitutes for it — decision: not in the §9.9 table, but judged to
cover 100% of real Pine usage of storing a series in a variable for reuse; `var`/`varip` and `:=`
have different semantics and are rejected). `ident = expr` keyword args (e.g. `title="Length"`)
are supported only inside call arguments.

Unsupported (all rejected as PineSyntaxError with (line, col)): namespaces outside the allow-list
such as `request.*`/`array.*`/`matrix.*`/`map.*`/`math.*`/`str.*`/`color.*`/`table.*`/`line.*`/
`box.*`; any `strategy.*` other than entry/exit, and `ns.ident` value references (only call form
allowed); bare function calls other than `plot` (`indicator()`/`strategy()` declaration calls
included — out of scope, §9.9 table, decision); user function definitions (`f(x) => ...`);
`if`/`for`/`while`/`switch` blocks; `:=`; `var`/`varip` declarations; the ternary operator `?:`.
"""
from __future__ import annotations

from collections.abc import Callable

from src.core.script.import_.pine.ast import (
    AssignStmt,
    BinaryExpr,
    BoolLiteral,
    CallArg,
    CallExpr,
    Expr,
    ExprStmt,
    Identifier,
    NotExpr,
    NumberLiteral,
    PineProgram,
    PostfixExpr,
    Statement,
    StringLiteral,
    UnaryExpr,
)
from src.core.script.import_.pine.lexer import PineSyntaxError, Token, TokenKind, tokenize

_TA_NAMESPACE = "ta"
_INPUT_NAMESPACE = "input"
_STRATEGY_NAMESPACE = "strategy"
_STRATEGY_ALLOWED_CALLS = frozenset({"entry", "exit"})
_BARE_CALL_ALLOWED = frozenset({"plot"})
_CMP_OPS = frozenset({"<", "<=", "==", "!=", ">=", ">"})
_ARITH_OPS = frozenset({"+", "-"})
_TERM_OPS = frozenset({"*", "/", "%"})


def parse(source: str) -> PineProgram:
    """Parses an entire Pine source into a `PineProgram` (`program := stmt*`,
    statements separated by newlines). Anything outside the supported
    grammar raises `PineSyntaxError`."""
    tokens = tokenize(source)
    parser = _Parser(tokens)
    statements: list[Statement] = []
    parser._skip_newlines()
    while parser._peek().kind is not TokenKind.EOF:
        statements.append(parser._statement())
        parser._skip_newlines()
    return PineProgram(statements=tuple(statements))


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

    def _check(self, kind: TokenKind, value: str | None = None, offset: int = 0) -> bool:
        tok = self._peek(offset)
        return tok.kind is kind and (value is None or tok.value == value)

    def _expect(self, kind: TokenKind, value: str | None, message: str) -> Token:
        if not self._check(kind, value):
            tok = self._peek()
            raise PineSyntaxError(message, tok.line, tok.col)
        return self._advance()

    def _skip_newlines(self) -> None:
        while self._check(TokenKind.NEWLINE):
            self._advance()

    # ---- statement := assign_stmt | expr_stmt ----

    def _statement(self) -> Statement:
        tok = self._peek()
        if tok.kind is TokenKind.KEYWORD and tok.value in ("var", "varip"):
            raise PineSyntaxError(
                "var/varip 영속 변수 선언은 지원하지 않습니다(단일 대입만 허용)",
                tok.line,
                tok.col,
            )
        if tok.kind is TokenKind.KEYWORD and tok.value in ("if", "for", "while", "switch"):
            raise PineSyntaxError(f"'{tok.value}' 블록은 지원하지 않습니다", tok.line, tok.col)

        if tok.kind is TokenKind.IDENT and self._peek(1).subtype == "ASSIGN":
            name = self._advance().value
            self._advance()  # "="
            stmt: Statement = AssignStmt(name=name, expr=self._expr())
        else:
            stmt = ExprStmt(expr=self._expr())

        end_tok = self._peek()
        if end_tok.kind is TokenKind.OP and end_tok.subtype == "ARROW":
            raise PineSyntaxError(
                "사용자 함수 정의('=>')는 지원하지 않습니다", end_tok.line, end_tok.col
            )
        if end_tok.kind is TokenKind.OP and end_tok.subtype == "REASSIGN":
            raise PineSyntaxError(
                "재대입 연산자 ':='는 지원하지 않습니다(var/varip 없이 단일 대입만 허용)",
                end_tok.line,
                end_tok.col,
            )
        if end_tok.kind not in (TokenKind.NEWLINE, TokenKind.EOF):
            raise PineSyntaxError(
                f"문장은 줄바꿈으로 끝나야 합니다(받음: {end_tok.value!r})",
                end_tok.line,
                end_tok.col,
            )
        return stmt

    # ---- expr := or_expr; or/and/cmp/arith/term are left-associative ----

    def _expr(self) -> Expr:
        return self._binary_keyword(self._and_expr, "or")

    def _and_expr(self) -> Expr:
        return self._binary_keyword(self._not_expr, "and")

    def _binary_keyword(self, operand: Callable[[], Expr], keyword: str) -> Expr:
        left = operand()
        while self._check(TokenKind.KEYWORD, keyword):
            self._advance()
            left = BinaryExpr(op=keyword, left=left, right=operand())
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
            return BinaryExpr(op=tok.value, left=left, right=self._arith())
        return left

    def _arith(self) -> Expr:
        return self._binary_op(self._term, _ARITH_OPS)

    def _term(self) -> Expr:
        return self._binary_op(self._unary, _TERM_OPS)

    def _binary_op(self, operand: Callable[[], Expr], ops: frozenset[str]) -> Expr:
        left = operand()
        while self._peek().kind is TokenKind.OP and self._peek().value in ops:
            op = self._advance().value
            left = BinaryExpr(op=op, left=left, right=operand())
        return left

    def _unary(self) -> Expr:
        if self._peek().kind is TokenKind.OP and self._peek().value == "-":
            self._advance()
            return UnaryExpr(op="-", operand=self._unary())
        return self._postfix()

    # postfix := primary ("[" INT "]")? — historical reference only (constant n>=0)
    def _postfix(self) -> Expr:
        base = self._primary()
        if not self._check(TokenKind.DELIM, "["):
            return base
        self._advance()
        idx_tok = self._peek()
        if idx_tok.kind is not TokenKind.NUMBER or "." in idx_tok.value:
            raise PineSyntaxError(
                "과거참조 인덱스는 0 이상 정수 상수만 허용합니다(음수·변수 인덱스 금지)",
                idx_tok.line,
                idx_tok.col,
            )
        self._advance()
        self._expect(TokenKind.DELIM, "]", "과거참조 인덱스 뒤에는 ']'가 필요합니다")
        return PostfixExpr(base=base, index=int(idx_tok.value))

    # primary := NUMBER | STRING | true | false | ident | call | qualified_call
    #          | "(" expr ")"
    def _primary(self) -> Expr:
        tok = self._peek()
        if tok.kind is TokenKind.NUMBER:
            self._advance()
            return NumberLiteral(value=_number_value(tok.value))
        if tok.kind is TokenKind.STRING:
            self._advance()
            return StringLiteral(value=tok.value)
        if tok.kind is TokenKind.KEYWORD and tok.value in ("true", "false"):
            self._advance()
            return BoolLiteral(value=tok.value == "true")
        if tok.kind is TokenKind.IDENT:
            if self._check(TokenKind.DELIM, ".", offset=1):
                return self._qualified()
            if self._check(TokenKind.DELIM, "(", offset=1):
                return self._bare_call()
            self._advance()
            return Identifier(name=tok.value)
        if tok.kind is TokenKind.DELIM and tok.value == "(":
            self._advance()
            expr = self._expr()
            self._expect(TokenKind.DELIM, ")", "'(' 뒤 표현식은 ')'로 닫아야 합니다")
            return expr
        raise PineSyntaxError(f"예상치 못한 토큰 {tok.value!r}", tok.line, tok.col)

    def _bare_call(self) -> CallExpr:
        """bare_call := IDENT "(" args ")" — only `plot` is allowed."""
        ident_tok = self._advance()
        if ident_tok.value not in _BARE_CALL_ALLOWED:
            raise PineSyntaxError(
                f"지원하지 않는 함수 호출입니다: {ident_tok.value}()"
                " (plot()만 네임스페이스 없이 호출할 수 있습니다)",
                ident_tok.line,
                ident_tok.col,
            )
        self._advance()  # "("
        args = self._args()
        return CallExpr(ns=None, ident=ident_tok.value, args=args)

    def _qualified(self) -> CallExpr:
        """qualified := ns "." ident "(" args ")" — ns is one of {ta, input, strategy}, and
        strategy allows only entry/exit. A non-call `ns.ident` value reference is rejected."""
        ns_tok = self._advance()
        self._advance()  # "."
        ident_tok = self._expect(
            TokenKind.IDENT, None, "네임스페이스 뒤에는 식별자가 필요합니다"
        )
        if not self._check(TokenKind.DELIM, "("):
            raise PineSyntaxError(
                f"{ns_tok.value}.{ident_tok.value}처럼 호출 없이 네임스페이스 값을"
                " 참조하는 표기는 지원하지 않습니다",
                ns_tok.line,
                ns_tok.col,
            )
        if ns_tok.value == _STRATEGY_NAMESPACE:
            if ident_tok.value not in _STRATEGY_ALLOWED_CALLS:
                raise PineSyntaxError(
                    f"strategy.{ident_tok.value}()는 지원하지 않습니다"
                    "(strategy.entry/exit만 허용)",
                    ns_tok.line,
                    ns_tok.col,
                )
        elif ns_tok.value not in (_TA_NAMESPACE, _INPUT_NAMESPACE):
            raise PineSyntaxError(
                f"지원하지 않는 네임스페이스입니다: {ns_tok.value} (ta/input/strategy만 허용)",
                ns_tok.line,
                ns_tok.col,
            )
        self._advance()  # "("
        args = self._args()
        return CallExpr(ns=ns_tok.value, ident=ident_tok.value, args=args)

    def _args(self) -> tuple[CallArg, ...]:
        args: list[CallArg] = []
        if not self._check(TokenKind.DELIM, ")"):
            args.append(self._arg())
            while self._check(TokenKind.DELIM, ","):
                self._advance()
                args.append(self._arg())
        self._expect(TokenKind.DELIM, ")", "호출 인자 뒤에는 ')'가 필요합니다")
        return tuple(args)

    def _arg(self) -> CallArg:
        """arg := (IDENT "=")? expr — Pine's keyword argument (e.g. `title="x"`)."""
        if self._check(TokenKind.IDENT) and self._peek(1).subtype == "ASSIGN":
            name = self._advance().value
            self._advance()  # "="
            return CallArg(name=name, value=self._expr())
        return CallArg(name=None, value=self._expr())
