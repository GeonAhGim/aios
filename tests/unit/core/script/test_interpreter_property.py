"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-8 DoD "참조 구현 대비 동일" —
property 테스트(시드 고정 무작위 프로그램, hypothesis 미설치라 `random.Random` 사용).

참조 구현은 인터프리터와 독립이다: 입력이 IR이 아니라 DSL-1 AST이고, 실행
전략이 벡터화 스택 머신이 아니라 "봉 t에서의 값"을 재귀적으로 묻는 트리
워커다. 시리즈 인덱싱을 `eval(base, t - n)`(부분식을 과거 봉에서 평가)로,
교차를 t·t-1 두 봉 평가로, 빌트인을 봉 단위 정의(sma = 과거 n봉 평균)로
정의한다 — `runtime/series.py` docstring의 의미론을 다른 각도에서 다시 적은
것이라 두 구현이 같은 답을 내면 의미론이 양쪽에서 일관됨을 뜻한다. 비교는
부동소수까지 정확 일치(같은 피연산자·같은 순서의 IEEE 연산).

생성기는 DSL-4 승격 격자대로 타입을 추적하며 표현식을 만들고, 만들어진
프로그램은 실제 `parse` 대신 AST를 직접 조립하되 `check_program`·`lower_program`
(DSL-4·7)을 그대로 통과시킨다. 생성기 결함(타입 오류)은 테스트 실패로 드러난다.
"""
from __future__ import annotations

import math
import random
from fractions import Fraction
from typing import cast

import pytest

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
    TypeNode,
    UnaryExpr,
)
from src.core.script.ir import lower_program
from src.core.script.runtime import (
    BuiltinRegistry,
    CallSite,
    Scalar,
    Series,
    Value,
    broadcast,
    execute,
)
from src.core.script.typing.types import Type, cmp_result, promote_bool, promote_numeric

BARS = 24
SERIES_INPUTS = ("close", "high")
INT_INPUT, FLOAT_INPUT = "length", "k"

# ---- 레지스트리(인터프리터용, 벡터화) ----


def _v_abs(args: tuple[Value, ...], site: CallSite) -> Value:
    def kernel(v: Scalar) -> Scalar:
        return None if v is None else float(abs(cast(float, v)))

    x = args[0]
    return x.map(kernel) if isinstance(x, Series) else kernel(x)


def _v_max(args: tuple[Value, ...], site: CallSite) -> Value:
    a, b = broadcast(args[0], site.bar_count).values, broadcast(args[1], site.bar_count).values
    out = [None if x is None or y is None else float(max(cast(float, x), cast(float, y)))
           for x, y in zip(a, b, strict=True)]
    return Series.of_floats(out)


def _v_sma(args: tuple[Value, ...], site: CallSite) -> Value:
    src, n = broadcast(args[0], site.bar_count).values, args[1]
    assert isinstance(n, int) and n >= 1
    out: list[float | None] = []
    for t in range(site.bar_count):
        window = src[t - n + 1 : t + 1] if t >= n - 1 else ()
        ok = bool(window) and all(w is not None for w in window)
        out.append(sum(cast(tuple[float, ...], window)) / n if ok else None)
    return Series.of_floats(out)


REGISTRY: BuiltinRegistry = {
    ("math", "abs"): _v_abs,
    ("math", "max"): _v_max,
    ("ta", "sma"): _v_sma,
}


# ---- 참조 구현(AST, 봉 단위 트리 워커) ----


def _norm(x: float) -> float | None:
    return x if math.isfinite(x) else None


class Reference:
    def __init__(self, program: Program, inputs: dict[str, object]) -> None:
        self.lets: dict[str, Expr] = {}
        self.inputs = inputs
        self.memo: dict[tuple[str, int], Scalar] = {}
        for decl in program.decls:
            if isinstance(decl, LetDecl | SignalDecl):
                self.lets[decl.name] = decl.expr

    def at(self, expr: Expr, t: int) -> Scalar:
        if t < 0:
            return None
        if isinstance(expr, NumberLiteral):
            return expr.value
        if isinstance(expr, Identifier):
            return self._ident(expr.name, t)
        if isinstance(expr, PostfixExpr):
            return self.at(expr.base, t - (expr.index or 0))
        if isinstance(expr, UnaryExpr):
            v = self.at(expr.operand, t)
            return None if v is None else (-v if isinstance(v, int) else _norm(-v))
        if isinstance(expr, NotExpr):
            b = self.at(expr.operand, t)
            return None if b is None else not b
        if isinstance(expr, BinaryExpr):
            return self._binary(expr, t)
        if isinstance(expr, CallExpr):
            return self._call(expr, t)
        raise AssertionError(expr)

    def _ident(self, name: str, t: int) -> Scalar:
        if name in self.inputs:
            src = self.inputs[name]
            return cast(Scalar, src[t] if isinstance(src, list) else src)
        key = (name, t)
        if key not in self.memo:
            self.memo[key] = self.at(self.lets[name], t)
        return self.memo[key]

    def _binary(self, expr: BinaryExpr, t: int) -> Scalar:
        op = expr.op
        if op in ("and", "or"):
            a, b = self.at(expr.left, t), self.at(expr.right, t)
            if op == "and":
                return False if a is False or b is False else (None if None in (a, b) else True)
            return True if a is True or b is True else (None if None in (a, b) else False)
        if op in ("crosses_above", "crosses_below"):
            a, b = self.at(expr.left, t), self.at(expr.right, t)
            pa, pb = self.at(expr.left, t - 1), self.at(expr.right, t - 1)
            if None in (a, b, pa, pb):
                return None
            a, b, pa, pb = (cast(float, x) for x in (a, b, pa, pb))
            return (a > b and pa <= pb) if op == "crosses_above" else (a < b and pa >= pb)
        a, b = self.at(expr.left, t), self.at(expr.right, t)
        if a is None or b is None:
            return None
        x, y = cast(float, a), cast(float, b)
        if op in ("<", "<=", "==", ">=", ">"):
            return {"<": x < y, "<=": x <= y, "==": x == y, ">=": x >= y, ">": x > y}[op]
        if isinstance(x, int) and isinstance(y, int):
            if op == "/":
                return None if y == 0 else math.trunc(Fraction(x, y))  # 정확한 0 방향 절삭
            return {"+": x + y, "-": x - y, "*": x * y}[op]
        if op == "/":
            return None if y == 0 else _norm(x / y)
        return _norm({"+": x + y, "-": x - y, "*": x * y}[op])

    def _call(self, expr: CallExpr, t: int) -> Scalar:
        name = (expr.ns, expr.ident)
        if name == ("math", "abs"):
            v = self.at(expr.args[0], t)
            return None if v is None else float(abs(cast(float, v)))
        if name == ("math", "max"):
            a, b = self.at(expr.args[0], t), self.at(expr.args[1], t)
            return None if a is None or b is None else float(max(cast(float, a), cast(float, b)))
        if name == ("ta", "sma"):
            n = cast(int, self.at(expr.args[1], t))
            window = [self.at(expr.args[0], t - i) for i in range(n - 1, -1, -1)]
            return None if any(w is None for w in window) else sum(cast(list[float], window)) / n
        raise AssertionError(name)


# ---- 타입 추적 생성기 ----


class Gen:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.num_names: list[tuple[str, Type]] = [(s, "series<float>") for s in SERIES_INPUTS]
        self.num_names += [(INT_INPUT, "int"), (FLOAT_INPUT, "float")]
        self.bool_names: list[tuple[str, Type]] = []

    def num(self, depth: int) -> tuple[Expr, Type]:
        r = self.rng
        choice = r.randrange(9) if depth > 0 else r.randrange(2)
        if choice == 0:
            return (NumberLiteral(value=r.randint(-3, 3)), "int") if r.random() < 0.5 else (
                NumberLiteral(value=round(r.uniform(-4, 4), 2)), "float")
        if choice == 1:
            name, t = r.choice(self.num_names)
            return Identifier(name=name), t
        if choice == 2:
            base, t = self.series_num(depth - 1)
            return PostfixExpr(base=base, index=r.randint(0, 3)), "float"
        if choice == 3:
            e, t = self.num(depth - 1)
            return UnaryExpr(op="-", operand=e), t
        if choice in (4, 5, 6):
            (le, lt), (re, rt) = self.num(depth - 1), self.num(depth - 1)
            op = cast(BinaryOp, r.choice(["+", "-", "*", "/"]))
            return BinaryExpr(op=op, left=le, right=re), _must(promote_numeric(lt, rt))
        if choice == 7:
            a, at = self.num(depth - 1)
            if r.random() < 0.5:
                return CallExpr(ns="math", ident="abs", args=(a,)), _call_type([at])
            b, bt = self.num(depth - 1)
            return CallExpr(ns="math", ident="max", args=(a, b)), _call_type([at, bt])
        a, at = self.num(depth - 1)
        n: Expr = Identifier(name=INT_INPUT)
        if r.random() < 0.7:
            n = NumberLiteral(value=r.randint(1, 4))
        return CallExpr(ns="ta", ident="sma", args=(a, n)), _call_type([at, "int"])

    def series_num(self, depth: int) -> tuple[Expr, Type]:
        for _ in range(8):
            e, t = self.num(depth)
            if t == "series<float>":
                return e, t
        return Identifier(name=SERIES_INPUTS[0]), "series<float>"

    def boolean(self, depth: int) -> tuple[Expr, Type]:
        r = self.rng
        choice = r.randrange(6) if depth > 0 else 0
        if choice == 0 or (choice == 1 and not self.bool_names):
            (le, lt), (re, rt) = self.num(depth - 1), self.num(depth - 1)
            ops = ["<", "<=", "==", ">=", ">", "crosses_above", "crosses_below"]
            op = cast(BinaryOp, r.choice(ops))
            return BinaryExpr(op=op, left=le, right=re), _must(cmp_result(lt, rt))
        if choice == 1:
            name, t = r.choice(self.bool_names)
            return Identifier(name=name), t
        if choice == 2:
            e, t = self.boolean(depth - 1)
            return NotExpr(operand=e), t
        if choice == 3:
            e, t = self.boolean(depth - 1)
            if t == "series<bool>":
                return PostfixExpr(base=e, index=r.randint(0, 2)), "bool"
            return e, t
        (le, lt), (re, rt) = self.boolean(depth - 1), self.boolean(depth - 1)
        op = cast(BinaryOp, r.choice(["and", "or"]))
        return BinaryExpr(op=op, left=le, right=re), _must(promote_bool(lt, rt))

    def program(self) -> Program:
        decls: list[Decl] = [
            InputDecl(name=s, type=TypeNode(name="series<float>"), value=0) for s in SERIES_INPUTS
        ]
        decls.append(InputDecl(name=INT_INPUT, type=TypeNode(name="int"), value=3))
        decls.append(InputDecl(name=FLOAT_INPUT, type=TypeNode(name="float"), value=1.5))
        for i in range(self.rng.randint(2, 6)):
            name = f"v{i}"
            if self.rng.random() < 0.6:
                e, t = self.num(self.rng.randint(1, 4))
                self.num_names.append((name, t))
            else:
                e, t = self.boolean(self.rng.randint(1, 4))
                self.bool_names.append((name, t))
            decls.append(LetDecl(name=name, expr=e))
        sig, st = self.boolean(3)
        decls.append(SignalDecl(name="go", expr=sig))
        self.bool_names.append(("go", st))
        decls.append(PlotDecl(expr=self.num(2)[0]))
        side, qty = Identifier(name="buy"), NumberLiteral(value=1)
        decls.append(OrderDecl(side=side, qty_expr=qty, when=self.boolean(2)[0]))
        return Program(decls=tuple(decls))


def _must(t: Type | None) -> Type:
    assert t is not None
    return t


def _call_type(arg_types: list[Type]) -> Type:
    return "series<float>" if any(t.startswith("series") for t in arg_types) else "float"


# ---- 비교 ----


def _series_of(rng: random.Random) -> list[float | None]:
    return [None if rng.random() < 0.08 else round(rng.uniform(-5, 5), 3) for _ in range(BARS)]


def _as_bars(value: Value) -> list[Scalar]:
    return list(value.values) if isinstance(value, Series) else [value] * BARS


def _same(a: Scalar, b: Scalar) -> bool:
    return a == b and type(a) is type(b) if a is not None and b is not None else a is b


def _check_program(seed: int) -> int:
    rng = random.Random(seed)
    program = Gen(rng).program()
    series = {s: _series_of(rng) for s in SERIES_INPUTS}
    scalars: dict[str, object] = {
        INT_INPUT: rng.randint(1, 4), FLOAT_INPUT: round(rng.uniform(-3, 3), 2)
    }
    ir = lower_program(program)
    inputs: dict[str, Value] = {s: Series.of_floats(v) for s, v in series.items()}
    inputs.update(cast(dict[str, Value], scalars))
    result = execute(ir, bar_count=BARS, inputs=inputs, builtins=REGISTRY)
    ref = Reference(program, {**series, **scalars})
    checked = 0
    for name in list(ref.lets):
        got = _as_bars(result.bindings[name])
        expected = [ref.at(Identifier(name=name), t) for t in range(BARS)]
        for t, (g, e) in enumerate(zip(got, expected, strict=True)):
            assert _same(g, e), f"seed={seed} {name}@{t}: interp={g!r} ref={e!r}"
        checked += 1
    plot = cast(PlotDecl, program.decls[-2])
    order = cast(OrderDecl, program.decls[-1])
    for got_v, expr in ((result.plots[0].value, plot.expr), (result.orders[0].when, order.when)):
        expected = [ref.at(expr, t) for t in range(BARS)]
        pairs = zip(_as_bars(got_v), expected, strict=True)
        assert all(_same(g, e) for g, e in pairs), f"seed={seed}"
    return checked


@pytest.mark.parametrize("seed", range(150))
def test_interpreter_matches_reference_on_random_programs(seed: int) -> None:
    assert _check_program(seed) >= 3


def test_generator_exercises_every_construct() -> None:
    kinds: set[str] = set()
    ops: set[str] = set()

    def visit(e: Expr) -> None:
        kinds.add(e.kind)
        fields = ("left", "right", "operand", "base")
        for child in (*(getattr(e, f, None) for f in fields), *getattr(e, "args", ())):
            if child is not None:
                visit(child)
        if isinstance(e, BinaryExpr):
            ops.add(e.op)
        if isinstance(e, CallExpr):
            ops.add(f"{e.ns}.{e.ident}")

    for seed in range(150):
        for decl in Gen(random.Random(seed)).program().decls:
            for expr in (getattr(decl, "expr", None), getattr(decl, "when", None)):
                if expr is not None:
                    visit(cast(Expr, expr))
    assert kinds == {"number", "ident", "call", "unary", "postfix", "not", "binary"}
    assert ops >= {"+", "-", "*", "/", "<", "<=", "==", ">=", ">", "crosses_above", "crosses_below",
                   "and", "or", "math.abs", "math.max", "ta.sma"}


def test_reference_detects_a_deliberately_wrong_semantics() -> None:
    """참조 구현이 실제로 차이를 잡아내는지(I-07): 시프트 방향을 바꾼 가짜 결과와 비교."""
    program = Program(decls=(
        InputDecl(name="close", type=TypeNode(name="series<float>"), value=0),
        LetDecl(name="v0", expr=PostfixExpr(base=Identifier(name="close"), index=1)),
    ))
    close = [1.0, 2.0, 3.0]
    ref = Reference(program, {"close": close})
    wrong = [2.0, 3.0, None]  # 미래 방향 시프트
    assert not all(_same(w, ref.at(Identifier(name="v0"), t)) for t, w in enumerate(wrong))
