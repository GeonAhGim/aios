"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-7 —
`ir/ops.py` + `ir/lower.py` 테스트.

DoD "AST→IR 결정론(같은 AST=같은 IR 바이트)"을 세 겹으로 단언한다: (1) 같은
소스를 두 번 파싱·로우어링한 바이트·해시 동일, (2) AST dict 키 순서를 뒤집어
복원해도 동일(딕셔너리 순서 비결정성 제거), (3) 서로 다른 `PYTHONHASHSEED`의
별도 프로세스에서도 동일(해시 시드 비결정성 제거). 그 외 명령 배치(post-order·
우선순위·인자 순서), 타입 주석(DSL-4 승격 결과), 의미 미정의 피연산자의 AST
원형 운반, 그리고 negative(타입 오류 전파·환경 불일치·미지원 노드·비유한
상수·스택 규율 위반)를 확인한다. 입력은 DSL-3 `parse()`의 실제 산출물이다.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from src.core.script.grammar.ast import (
    Expr,
    Identifier,
    InputDecl,
    LetDecl,
    NumberLiteral,
    Program,
    TypeNode,
    program_from_dict,
)
from src.core.script.grammar.parser import parse
from src.core.script.ir import (
    IR_VERSION,
    BinOp,
    Call,
    ConstFloat,
    ConstInt,
    DeclareInput,
    Index,
    IRProgram,
    IRStackError,
    Load,
    Order,
    Plot,
    ScriptLowerError,
    Signal,
    Store,
    from_bytes,
    lower_expr,
    lower_program,
    to_bytes,
    verify_stack,
)
from src.core.script.typing.checker import ScriptTypeError, check_program

_REPO_ROOT = Path(__file__).resolve().parents[4]

SAMPLE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "let prev = close[1]\n"
    "signal go_long = rsi_val < 30 and close > prev\n"
    "plot(rsi_val, 1)\n"
    "order(buy, 1, 2) when go_long"
)


def _sha(source: str) -> str:
    return hashlib.sha256(to_bytes(lower_program(parse(source)))).hexdigest()


def _first_let_expr(source: str) -> Expr:
    decl = parse(source).decls[0]
    assert isinstance(decl, LetDecl)
    return decl.expr


# ---- 결정론 ----


def test_same_source_lowers_to_identical_bytes_and_hash() -> None:
    first = to_bytes(lower_program(parse(SAMPLE)))
    second = to_bytes(lower_program(parse(SAMPLE)))
    assert first == second
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(second).hexdigest()


def test_ast_dict_key_order_does_not_change_bytes() -> None:
    program = parse(SAMPLE)

    def _reverse(obj: object) -> object:
        if isinstance(obj, dict):
            return {k: _reverse(obj[k]) for k in reversed(list(obj))}
        if isinstance(obj, list):
            return [_reverse(x) for x in obj]
        return obj

    reversed_dict = cast(dict[str, object], _reverse(program.model_dump(mode="json")))
    shuffled = program_from_dict(reversed_dict)
    assert shuffled == program
    assert to_bytes(lower_program(shuffled)) == to_bytes(lower_program(program))


def test_bytes_identical_across_processes_with_different_hash_seeds() -> None:
    code = (
        "import hashlib, sys;"
        "from src.core.script.grammar.parser import parse;"
        "from src.core.script.ir import lower_program, to_bytes;"
        "src = sys.stdin.read();"
        "sys.stdout.write(hashlib.sha256(to_bytes(lower_program(parse(src)))).hexdigest())"
    )
    digests = []
    for seed in ("1", "424242"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(  # noqa: S603 — 고정 인자, 자기 인터프리터
            [sys.executable, "-c", code],
            input=SAMPLE,
            cwd=_REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=True,
        )
        digests.append(result.stdout.strip())
    assert digests[0] == digests[1]
    assert digests[0] == _sha(SAMPLE)


def test_bytes_round_trip_restores_equal_ir() -> None:
    ir = lower_program(parse(SAMPLE))
    restored = from_bytes(to_bytes(ir))
    assert restored == ir
    assert to_bytes(restored) == to_bytes(ir)
    assert restored.ir_version == IR_VERSION


def test_semantically_different_asts_have_different_bytes() -> None:
    assert _sha("let a = 1 + 2") != _sha("let a = 2 + 1")
    assert _sha("let a = 1") != _sha("let a = 1.0")


# ---- 명령 배치: post-order·우선순위·인자 순서 ----


def test_arith_precedence_lowers_post_order() -> None:
    ir = lower_program(parse("let a = 1\nlet b = a + 2 * 3"))
    assert ir.instrs[2:] == (
        Load(name="a", type="int"),
        ConstInt(value=2),
        ConstInt(value=3),
        BinOp(operator="*", type="int"),
        BinOp(operator="+", type="int"),
        Store(name="b", type="int"),
    )


def test_call_args_lower_left_to_right_before_call() -> None:
    ir = lower_program(parse("input c: series<float> = 0\nlet x = ta.sma(c, 20)"))
    assert ir.instrs[1:] == (
        Load(name="c", type="series<float>"),
        ConstInt(value=20),
        Call(ns="ta", ident="sma", argc=2, type="series<float>"),
        Store(name="x", type="series<float>"),
    )


def test_postfix_index_emits_index_with_element_type() -> None:
    ir = lower_program(parse("input c: series<float> = 0\nlet p = c[2]"))
    assert ir.instrs[1:3] == (
        Load(name="c", type="series<float>"),
        Index(offset=2, type="float"),
    )


def test_postfix_without_index_emits_no_index_op() -> None:
    ir = lower_program(parse("input c: series<float> = 0\nlet p = (c)"))
    assert not any(isinstance(i, Index) for i in ir.instrs)


def test_float_literal_and_unary_minus() -> None:
    instrs = lower_expr(_first_let_expr("let a = -1.5"), {})
    assert instrs[0] == ConstFloat(value=1.5)
    assert instrs[1].op == "neg"


# ---- 타입 주석: DSL-4 승격 결과가 IR에 실린다 ----


def test_series_promotion_is_annotated_on_binop_and_signal() -> None:
    ir = lower_program(parse("input c: series<float> = 0\nsignal g = c > 1 and 1 < 2"))
    binops = [i for i in ir.instrs if isinstance(i, BinOp)]
    assert [b.type for b in binops] == ["series<bool>", "bool", "series<bool>"]
    assert ir.instrs[-1] == Signal(name="g", type="series<bool>")


def test_input_decl_lowers_to_declare_input_with_value() -> None:
    ir = lower_program(parse("input n: int = 14\ninput k: float = 2.5"))
    assert ir.instrs == (
        DeclareInput(name="n", type="int", value=14),
        DeclareInput(name="k", type="float", value=2.5),
    )


def test_bool_input_value_is_kept_distinct_from_int_in_bytes() -> None:
    # §3.3 primary에 bool 리터럴이 없어 파서는 `= 1`만 만들지만, DSL-1 InputDecl.value는
    # bool도 허용한다 — IR 바이트는 true와 1을 구분해야 한다(같은 AST=같은 바이트의 역).
    as_bool = Program(decls=(InputDecl(name="f", type=TypeNode(name="bool"), value=True),))
    as_int = Program(decls=(InputDecl(name="f", type=TypeNode(name="bool"), value=1),))
    assert lower_program(as_bool).instrs == (DeclareInput(name="f", type="bool", value=True),)
    assert to_bytes(lower_program(as_bool)) != to_bytes(lower_program(as_int))


# ---- 의미 미정의 피연산자는 AST 원형으로 운반된다 ----


def test_order_carries_side_qty_opts_as_ast_and_lowers_only_when() -> None:
    ir = lower_program(parse("order(buy, 1, 2) when 1 < 2"))
    order = ir.instrs[-1]
    assert isinstance(order, Order)
    assert order.side == Identifier(name="buy")
    assert order.qty_expr == NumberLiteral(value=1)
    assert order.opts == NumberLiteral(value=2)
    assert order.when_type == "bool"
    assert not any(isinstance(i, Load) and i.name == "buy" for i in ir.instrs)


def test_plot_carries_style_as_ast() -> None:
    ir = lower_program(parse("plot(1, 7)"))
    assert ir.instrs[-1] == Plot(type="int", style=NumberLiteral(value=7))


# ---- negative ----


def test_ill_typed_program_is_rejected_with_script_type_error() -> None:
    with pytest.raises(ScriptTypeError):
        lower_program(parse("let bad = not 1"))


def test_mismatched_env_is_rejected() -> None:
    program = parse("let a = 1")
    ok = lower_program(program, check_program(program))
    assert ok.instrs[-1] == Store(name="a", type="int")
    with pytest.raises(ScriptLowerError):
        lower_program(program, {"a": "float"})


def test_non_program_input_is_rejected() -> None:
    with pytest.raises(ScriptLowerError):
        lower_program(cast(Program, "let a = 1"))


def test_unsupported_expr_node_is_rejected() -> None:
    foreign = cast(Expr, SimpleNamespace(kind="binary", op="**"))
    with pytest.raises(ScriptLowerError):
        lower_expr(foreign, {})


def test_non_finite_number_literal_is_rejected() -> None:
    with pytest.raises(ScriptLowerError):
        lower_expr(NumberLiteral(value=float("inf")), {})
    huge = "9" * 400 + ".5"
    with pytest.raises(ScriptLowerError):
        lower_program(parse(f"let a = {huge}"))


def test_verify_stack_rejects_underflow_and_leftover() -> None:
    with pytest.raises(IRStackError):
        verify_stack(IRProgram(instrs=(Store(name="a", type="int"),)))
    with pytest.raises(IRStackError):
        verify_stack(
            IRProgram(instrs=(ConstInt(value=1), ConstInt(value=2), Store(name="a", type="int")))
        )
    with pytest.raises(IRStackError):
        verify_stack(IRProgram(instrs=(ConstInt(value=1),)))


def test_from_bytes_rejects_foreign_version_and_non_finite_constant() -> None:
    good = to_bytes(lower_program(parse("let a = 1.5")))
    with pytest.raises(ValueError):
        from_bytes(good.replace(b'"aios-ir-1"', b'"aios-ir-0"'))
    with pytest.raises(ValueError):
        from_bytes(good.replace(b"1.5", b"Infinity"))
