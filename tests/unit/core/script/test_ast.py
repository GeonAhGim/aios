"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-1 —
`grammar/ast.py` 테스트.

직렬화 왕복(§3.3 전 프로덕션을 최소 1회씩 사용)이 항등임을 단언하고,
불변성·postfix 과거참조(`[n]`은 상수 n>=0만) 제약을 negative test로
검증한다. DEPTH 감사(task-2727) D2 보강: 실패 주입(직렬화 도중 예외
전파)·수치 성능 단언(왕복 지연 예산)·게이트 적색 재현(`extra=forbid`
회귀 시뮬레이션)을 추가한다.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from src.core.script.grammar.ast import (
    GRAMMAR_VERSION,
    BinaryExpr,
    CallExpr,
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
    program_from_dict,
    to_dict,
)


def _sample_program() -> Program:
    """§3.3 전 프로덕션(or/and/not/cmp/arith/term/unary/postfix/call/type)을
    최소 1회씩 사용하는 프로그램 — decl 5종도 전부 포함한다."""
    close = Identifier(name="close")
    rsi = CallExpr(ns="ta", ident="rsi", args=(close, NumberLiteral(value=14)))
    prev_close = PostfixExpr(base=close, index=1)
    below_30 = BinaryExpr(op="<", left=rsi, right=NumberLiteral(value=30))
    rising = BinaryExpr(op=">", left=close, right=prev_close)
    crossed = BinaryExpr(op="crosses_above", left=close, right=prev_close)
    cond = BinaryExpr(op="and", left=below_30, right=rising)
    negated = NotExpr(operand=BinaryExpr(op="or", left=cond, right=crossed))
    arith = BinaryExpr(
        op="+",
        left=BinaryExpr(op="*", left=NumberLiteral(value=2), right=close),
        right=UnaryExpr(op="-", operand=NumberLiteral(value=1)),
    )
    return Program(
        decls=(
            InputDecl(name="length", type=TypeNode(name="int"), value=14),
            LetDecl(name="score", expr=arith),
            PlotDecl(expr=Identifier(name="score"), style=None),
            SignalDecl(name="entry", expr=negated),
            OrderDecl(
                side=Identifier(name="buy"),
                qty_expr=NumberLiteral(value=1),
                opts=None,
                when=cond,
            ),
        ),
    )


def test_serialization_round_trip_is_identity() -> None:
    program = _sample_program()

    restored = program_from_dict(to_dict(program))

    assert restored == program
    assert to_dict(restored) == to_dict(program)


def test_grammar_version_is_single_sourced_and_stamped() -> None:
    program = _sample_program()
    assert GRAMMAR_VERSION == "aios-script-1"
    assert program.grammar_version == GRAMMAR_VERSION


@pytest.mark.parametrize(
    "make_node",
    [
        lambda: NumberLiteral(value=1),
        lambda: Identifier(name="close"),
        lambda: _sample_program(),
    ],
)
def test_nodes_are_immutable(make_node: Any) -> None:
    node = make_node()
    field_name = next(iter(type(node).model_fields))
    with pytest.raises(ValidationError):
        setattr(node, field_name, node.model_dump()[field_name])


def test_postfix_rejects_negative_index() -> None:
    with pytest.raises(ValidationError):
        PostfixExpr(base=Identifier(name="close"), index=-1)


def test_postfix_allows_zero_and_positive_constant_index() -> None:
    assert PostfixExpr(base=Identifier(name="close"), index=0).index == 0
    assert PostfixExpr(base=Identifier(name="close"), index=5).index == 5


def test_postfix_index_cannot_be_a_variable() -> None:
    """인덱스 필드 타입이 `int | None`이라 변수 참조(Expr) 자체를 담을 수
    없다 — 구조 수준에서 "변수 인덱스 금지"가 강제된다는 것을 확인한다."""
    with pytest.raises(ValidationError):
        PostfixExpr.model_validate(
            {
                "kind": "postfix",
                "base": {"kind": "ident", "name": "close"},
                "index": {"kind": "ident", "name": "n"},
            }
        )


def test_identifier_rejects_invalid_name() -> None:
    with pytest.raises(ValidationError):
        Identifier(name="1bad-name")


@pytest.mark.parametrize("bad_value", [True, False])
def test_number_literal_rejects_bool_despite_int_subclass(bad_value: bool) -> None:
    """`bool`은 `int`의 서브클래스라 승격에 노출되기 쉽다 — §3.3 `primary`에
    원시 bool 리터럴이 없다는 불변식은 값 수준에서도 강제되어야 한다."""
    with pytest.raises(ValidationError):
        NumberLiteral(value=bad_value)


def test_postfix_index_rejects_bool_despite_int_subclass() -> None:
    with pytest.raises(ValidationError):
        PostfixExpr(base=Identifier(name="close"), index=True)


def test_program_rejects_unknown_field() -> None:
    data = to_dict(_sample_program())
    data["unexpected"] = True
    with pytest.raises(ValidationError):
        program_from_dict(data)


def test_program_rejects_grammar_version_mismatch() -> None:
    data = to_dict(_sample_program())
    data["grammar_version"] = "aios-script-2"
    with pytest.raises(ValidationError):
        program_from_dict(data)


def test_to_dict_propagates_serialization_failure_instead_of_masking_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: 직렬화 도중(예: pydantic 내부 회귀) 예외가 나면 `to_dict`가
    빈 dict나 부분 결과로 조용히 대체하지 않고 예외를 그대로 전파해야 한다
    — fail-closed. 손상된 산출물을 성공으로 위장하는 것이 가장 위험하다."""
    program = _sample_program()

    def _boom(self: Program, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("simulated corrupted serialization")

    monkeypatch.setattr(Program, "model_dump", _boom)

    with pytest.raises(RuntimeError, match="simulated corrupted serialization"):
        to_dict(program)


@pytest.mark.perf
def test_serialization_round_trip_latency_is_bounded() -> None:
    """수치 성능 단언: 중간 크기 프로그램의 `to_dict`→`program_from_dict`
    왕복 평균 지연이 예산(2ms/회)을 넘지 않는다 — 우연한 이차 복잡도 회귀나
    검증기 재구성 누락을 조기에 검출한다."""
    program = _sample_program()

    for _ in range(20):  # JIT/캐시 워밍업 — 첫 호출 지연을 측정에서 배제
        program_from_dict(to_dict(program))

    iterations = 500
    start = time.perf_counter()
    for _ in range(iterations):
        program_from_dict(to_dict(program))
    elapsed_s = time.perf_counter() - start

    avg_ms = (elapsed_s / iterations) * 1000
    assert avg_ms < 2.0, f"직렬화 왕복 평균 {avg_ms:.3f}ms > 2.0ms 예산"


def test_extra_forbid_regression_would_be_caught_by_unknown_field_guard() -> None:
    """게이트 적색 재현: `test_program_rejects_unknown_field`가 실제로
    `ScriptNode`의 `extra=forbid` 설정에 의존한다는 것을 증명한다.
    `extra=forbid`가 빠진 회귀를 시뮬레이션하면 같은 손상 입력(미지 필드)이
    조용히 통과해 그 가드 테스트가 빨간불이 됨을 보여준다."""

    class _RegressedScriptNode(BaseModel):
        model_config = ConfigDict(frozen=True, extra="allow")

    class _RegressedProgram(_RegressedScriptNode):
        kind: str = "program"
        grammar_version: str = GRAMMAR_VERSION
        decls: tuple[Any, ...] = ()

    data = to_dict(_sample_program())
    data["unexpected"] = True

    # 회귀 상태(extra=allow)에서는 미지 필드가 거부되지 않고 조용히 통과한다
    # — 이 라인이 예외 없이 성공하는 것 자체가 "가드가 빨간불이 됨"이다.
    regressed = _RegressedProgram.model_validate(data)
    assert regressed.model_dump()["unexpected"] is True

    # 실제 Program은 extra=forbid를 상속하므로 동일 입력을 여전히 거부한다.
    with pytest.raises(ValidationError):
        program_from_dict(data)
