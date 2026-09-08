"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-9b —
`runtime/builtins_strategy.py` 테스트.

확인 항목(task-2140 DoD 1:1): (a) 결정론 — 같은 스크립트·같은 입력을 두 번 실행한
의도 목록의 정규화 직렬화가 바이트 동일, (b) `strategy.entry`의 수량이 0
이하이거나 na면 값을 만들지 않고 `SCRIPT_BUILTIN_ARG`로 거부, (c) side/qty가
`Series`(봉마다 다름)이면 컴파일 타입과 무관하게 `SCRIPT_STRATEGY_NONCONSTANT`로
거부, (d) `strategy.*` 호출 인자의 `[-1]` 같은 미래참조가 DSL-5 lookahead를
우회하지 않음, (e) 이 파일에 DB·네트워크·시계 임포트가 없고 자기 자신을
재귀호출하지 않음(AST 정적 검사), (f) 등록된 함수 표가 entry/exit/close/order
4종 정확히 일치.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.core.script.analysis.lookahead import ScriptLookaheadError, check_source
from src.core.script.ir import Call, ConstInt, IRProgram, Store
from src.core.script.runtime import (
    STRATEGY_KINDS,
    BuiltinCallError,
    CallSite,
    Series,
    StrategyBuiltins,
    StrategyIntent,
    Value,
    builtins_strategy,
    execute,
    intents_to_bytes,
)

_MODULE = Path(builtins_strategy.__file__)


def call(kind: str, *args: Value, bars: int = 4) -> tuple[Value, StrategyBuiltins]:
    sb = StrategyBuiltins()
    result = sb.table[("strategy", kind)](args, CallSite("strategy", kind, "float", bars))
    return result, sb


# ---- (f) 표 구성 ----


def test_table_contains_exactly_the_documented_functions() -> None:
    sb = StrategyBuiltins()
    assert set(STRATEGY_KINDS) == {"entry", "exit", "close", "order"}
    assert set(sb.table) == {("strategy", kind) for kind in STRATEGY_KINDS}


# ---- 정상 호출: 의도 기록 + 반환값 ----


@pytest.mark.parametrize(
    ("kind", "args", "expected_return", "expected_side", "expected_qty"),
    [
        ("entry", (1, 10), 10.0, "long", 10.0),
        ("entry", (-1, 2.5), 2.5, "short", 2.5),
        ("order", (1, 5), 5.0, "long", 5.0),
        ("order", (-1, 5), 5.0, "short", 5.0),
        ("exit", (3,), 3.0, None, 3.0),
        ("close", (4,), 4.0, None, 4.0),
    ],
)
def test_accepted_call_records_intent_and_returns_qty(
    kind: str,
    args: tuple[Value, ...],
    expected_return: float,
    expected_side: str | None,
    expected_qty: float,
) -> None:
    result, sb = call(kind, *args)
    assert result == expected_return
    assert sb.intents == (
        StrategyIntent(kind=kind, side=expected_side, qty=expected_qty, call_index=0),
    )


def test_close_without_qty_records_none_and_returns_zero() -> None:
    result, sb = call("close")
    assert result == 0.0
    assert sb.intents == (StrategyIntent(kind="close", side=None, qty=None, call_index=0),)


def test_call_index_is_ordinal_across_calls_on_one_instance() -> None:
    sb = StrategyBuiltins()
    sb.table[("strategy", "entry")]((1, 1), CallSite("strategy", "entry", "float", 4))
    sb.table[("strategy", "exit")]((2,), CallSite("strategy", "exit", "float", 4))
    assert [intent.call_index for intent in sb.intents] == [0, 1]


# ---- negative: arity ----


@pytest.mark.parametrize(
    ("kind", "args"),
    [
        ("entry", ()),
        ("entry", (1,)),
        ("entry", (1, 2, 3)),
        ("order", (1,)),
        ("exit", ()),
        ("exit", (1, 2)),
        ("close", (1, 2)),
    ],
)
def test_wrong_arity_is_rejected(kind: str, args: tuple[Value, ...]) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call(kind, *args)
    assert info.value.reason == "SCRIPT_BUILTIN_ARITY"


# ---- negative: (b) 0 이하·na 수량 거부(허용하면 실패) ----


@pytest.mark.parametrize("bad_qty", [0, -1, -0.5, None])
def test_entry_rejects_non_positive_or_na_qty(bad_qty: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, bad_qty)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


def test_entry_rejects_bool_qty() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, True)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize("bad_side", [0, 2, -2, 1.5, None, True])
def test_entry_rejects_side_outside_long_short_encoding(bad_side: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", bad_side, 1)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


# ---- negative: (c) 상수 강제 — Series 인자는 타입과 무관하게 거부 ----


def test_entry_rejects_series_qty_even_when_statically_scalar() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, Series.of_floats([1.0, 2.0, 3.0, 4.0]))
    assert info.value.reason == "SCRIPT_STRATEGY_NONCONSTANT"


def test_entry_rejects_series_side() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", Series.of_floats([1.0, 1.0, 1.0, 1.0]), 1)
    assert info.value.reason == "SCRIPT_STRATEGY_NONCONSTANT"


# ---- (a) 결정론: 같은 IR·같은 입력 → 바이트 동일 ----
#
# `grammar/parser.py`의 `_NAMESPACES`(DSL-3, 이 리프가 건드리지 않는 파일)는
# 지금 `{ta, math, series}`만 허용해 `strategy.*` 호출은 아직 AIOS Script
# *소스 텍스트*로는 쓸 수 없다(모듈 docstring "wiring" 절 참조). `ir.ops.Call`은
# `ns: str`에 그런 제약이 없으므로, 이 표를 실제로 쓰는 방법은 지금은 IR을
# 직접 조립하는 호스트뿐이다 — 이 테스트가 그 경로를 그대로 재현한다.


def _build_ir() -> IRProgram:
    return IRProgram(
        instrs=(
            ConstInt(value=1),
            ConstInt(value=10),
            Call(ns="strategy", ident="entry", argc=2, type="float"),
            Store(name="q1", type="float"),
            ConstInt(value=3),
            Call(ns="strategy", ident="exit", argc=1, type="float"),
            Store(name="q2", type="float"),
            Call(ns="strategy", ident="close", argc=0, type="float"),
            Store(name="q3", type="float"),
        )
    )


def _run_ir() -> tuple[object, StrategyBuiltins]:
    sb = StrategyBuiltins()
    result = execute(_build_ir(), bar_count=4, builtins=sb.table)
    return result, sb


def test_same_ir_same_input_yields_byte_identical_intents_twice() -> None:
    _, sb1 = _run_ir()
    _, sb2 = _run_ir()
    assert sb1.intents == sb2.intents
    assert intents_to_bytes(sb1.intents) == intents_to_bytes(sb2.intents)
    assert len(sb1.intents) == 3


def test_wired_through_interpreter_execute() -> None:
    result, sb = _run_ir()
    assert result.bindings["q1"] == 10.0
    assert result.bindings["q2"] == 3.0
    assert result.bindings["q3"] == 0.0
    assert [intent.kind for intent in sb.intents] == ["entry", "exit", "close"]


# ---- (d) 미래참조 무결성: strategy.* 경유로 lookahead 우회 불가 ----


def test_lookahead_detection_is_not_bypassed_via_strategy_namespace() -> None:
    with pytest.raises(ScriptLookaheadError):
        check_source("let q = strategy.entry(1, close[-1])")


def test_lookahead_variable_index_is_not_bypassed_via_strategy_namespace() -> None:
    with pytest.raises(ScriptLookaheadError):
        check_source("let q = strategy.exit(close[i])")


# ---- (e) 순수성: I/O·시계 임포트 없음 + 자기 재귀 없음 ----

_ALLOWED_IMPORT_PREFIXES = (
    "__future__", "collections.abc", "dataclasses", "json", "math", "typing",
    "src.core.script.",
)  # fmt: skip


def test_module_imports_no_io_or_clock_and_never_calls_itself() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert name.startswith(_ALLOWED_IMPORT_PREFIXES), f"I/O 가능 import: {name}"
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call_node in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
            callee = call_node.func
            own = (isinstance(callee, ast.Name) and callee.id == func.name) or (
                isinstance(callee, ast.Attribute) and callee.attr == func.name
            )
            assert not own, f"{func.name}가 자기 자신을 호출(재귀)"
            assert not (isinstance(callee, ast.Name) and callee.id in {"open", "exec", "eval"})
