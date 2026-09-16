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

DEEPEN(task-2929, docs/audit/DEPTH_DSL_IND.md#2140): D2 하한 중 negative(8건)는
이미 충분하다고 판정됐고, 나머지 세 축을 이 파일에서 채운다 -- 실패 주입 1건
(`test_intents_to_bytes_fails_closed_on_a_corrupted_nan_qty`), 수치 성능 단언
1건(`test_twenty_thousand_calls_and_serialization_complete_within_one_second`),
게이트 적색 재현 1건(`test_purity_gate_flags_injected_violation` -- (e)의 AST
순수성 검사가 이미 깨끗한 실제 모듈에서만 공허하게 통과하는 게 아니라 실제
위반을 넣으면 잡아낸다는 것을 증명).
"""

from __future__ import annotations

import ast
import time
from decimal import Decimal
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
    assert set(STRATEGY_KINDS) == {"entry", "exit", "close", "order", "bracket"}
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


# ---- task-2623: limit/stop trigger price on entry/order/exit ----


@pytest.mark.parametrize(
    ("kind", "args", "expected_side", "expected_order_type", "expected_trigger"),
    [
        ("entry", (1, 10, 100, 1), "long", "limit", 100.0),
        ("entry", (-1, 5, 50, -1), "short", "stop", 50.0),
        ("order", (1, 5, 20, 1), "long", "limit", 20.0),
        ("order", (-1, 5, 20, -1), "short", "stop", 20.0),
    ],
)
def test_entry_order_accept_trigger_price_and_type_code(
    kind: str,
    args: tuple[Value, ...],
    expected_side: str,
    expected_order_type: str,
    expected_trigger: float,
) -> None:
    result, sb = call(kind, *args)
    assert result == args[1]
    intent = sb.intents[0]
    assert intent.side == expected_side
    assert intent.order_type == expected_order_type
    assert intent.trigger_price == Decimal(str(expected_trigger))


@pytest.mark.parametrize(
    ("args", "expected_order_type", "expected_trigger"),
    [
        ((3, 90, 1), "limit", 90.0),
        ((3, 80, -1), "stop", 80.0),
    ],
)
def test_exit_accepts_trigger_price_and_type_code(
    args: tuple[Value, ...], expected_order_type: str, expected_trigger: float
) -> None:
    result, sb = call("exit", *args)
    assert result == args[0]
    intent = sb.intents[0]
    assert intent.kind == "exit"
    assert intent.side is None
    assert intent.order_type == expected_order_type
    assert intent.trigger_price == Decimal(str(expected_trigger))


def test_market_entry_defaults_order_type_and_trigger_price() -> None:
    _, sb = call("entry", 1, 10)
    intent = sb.intents[0]
    assert intent.order_type == "market"
    assert intent.trigger_price is None


@pytest.mark.parametrize(
    ("kind", "args"),
    [
        ("entry", (1, 10, 100)),  # 3 args: trigger_price without type_code
        ("order", (1, 10, 100)),
        ("exit", (3, 100)),  # 2 args: trigger_price without type_code
    ],
)
def test_trigger_price_and_type_code_are_all_or_nothing(kind: str, args: tuple[Value, ...]) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call(kind, *args)
    assert info.value.reason == "SCRIPT_BUILTIN_ARITY"


@pytest.mark.parametrize("bad_type_code", [0, 2, -2, 1.5, None])
def test_entry_rejects_type_code_outside_limit_stop_encoding(bad_type_code: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, 10, 100, bad_type_code)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize("bad_trigger", [0, -1, None])
def test_entry_rejects_non_positive_or_na_trigger_price(bad_trigger: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, 10, bad_trigger, 1)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


def test_entry_rejects_series_trigger_price_even_when_statically_scalar() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, 10, Series.of_floats([100.0, 100.0, 100.0, 100.0]), 1)
    assert info.value.reason == "SCRIPT_STRATEGY_NONCONSTANT"


def test_entry_rejects_series_type_code() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("entry", 1, 10, 100, Series.of_floats([1.0, 1.0, 1.0, 1.0]))
    assert info.value.reason == "SCRIPT_STRATEGY_NONCONSTANT"


# ---- task-2623: strategy.bracket(qty, profit_price, loss_price, trail_pct) ----


def test_bracket_records_all_three_legs() -> None:
    result, sb = call("bracket", 10, 120, 90, 0.05)
    assert result == 10.0
    intent = sb.intents[0]
    assert intent.kind == "bracket"
    assert intent.side is None
    assert intent.qty == Decimal("10")
    assert intent.profit_price == Decimal("120")
    assert intent.loss_price == Decimal("90")
    assert intent.trail_pct == Decimal("0.05")


@pytest.mark.parametrize(
    ("args", "expected_profit", "expected_loss", "expected_trail"),
    [
        ((10, 120, None, None), Decimal("120"), None, None),
        ((10, None, 90, None), None, Decimal("90"), None),
        ((10, None, None, 0.05), None, None, Decimal("0.05")),
        ((10, 120, 90, None), Decimal("120"), Decimal("90"), None),
    ],
)
def test_bracket_accepts_any_nonempty_subset_of_legs(
    args: tuple[Value, ...],
    expected_profit: Decimal | None,
    expected_loss: Decimal | None,
    expected_trail: Decimal | None,
) -> None:
    _, sb = call("bracket", *args)
    intent = sb.intents[0]
    assert intent.profit_price == expected_profit
    assert intent.loss_price == expected_loss
    assert intent.trail_pct == expected_trail


def test_bracket_rejects_all_three_legs_na() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", 10, None, None, None)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize(("bad_qty"), [0, -1, None])
def test_bracket_rejects_non_positive_or_na_qty(bad_qty: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", bad_qty, 120, None, None)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize("bad_price", [0, -1])
def test_bracket_rejects_non_positive_profit_or_loss_price(bad_price: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", 10, bad_price, None, None)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize("bad_trail", [0, 1, 1.5, -0.1])
def test_bracket_rejects_trail_pct_outside_open_unit_interval(bad_trail: Value) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", 10, None, None, bad_trail)
    assert info.value.reason == "SCRIPT_BUILTIN_ARG"


@pytest.mark.parametrize(
    "args",
    [(), (10,), (10, 120), (10, 120, 90), (10, 120, 90, 0.05, 1)],
)
def test_bracket_wrong_arity_is_rejected(args: tuple[Value, ...]) -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", *args)
    assert info.value.reason == "SCRIPT_BUILTIN_ARITY"


def test_bracket_rejects_series_profit_price() -> None:
    with pytest.raises(BuiltinCallError) as info:
        call("bracket", 10, Series.of_floats([120.0, 120.0, 120.0, 120.0]), None, None)
    assert info.value.reason == "SCRIPT_STRATEGY_NONCONSTANT"


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
    "__future__", "collections.abc", "dataclasses", "decimal", "json", "math", "typing",
    "src.core.script.",
)  # fmt: skip


def _purity_violations(source: str) -> list[str]:
    """(e) 검사의 실제 판정 로직 -- 실제 모듈과 아래 게이트 적색 재현 테스트의
    오염된 샘플이 같은 코드 경로로 검사받도록 분리해 둔다."""
    violations: list[str] = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            if not name.startswith(_ALLOWED_IMPORT_PREFIXES):
                violations.append(f"I/O 가능 import: {name}")
    for func in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        for call_node in [n for n in ast.walk(func) if isinstance(n, ast.Call)]:
            callee = call_node.func
            own = (isinstance(callee, ast.Name) and callee.id == func.name) or (
                isinstance(callee, ast.Attribute) and callee.attr == func.name
            )
            if own:
                violations.append(f"{func.name}가 자기 자신을 호출(재귀)")
            if isinstance(callee, ast.Name) and callee.id in {"open", "exec", "eval"}:
                violations.append(f"{func.name}가 위험 호출 사용: {callee.id}")
    return violations


def test_module_imports_no_io_or_clock_and_never_calls_itself() -> None:
    assert _purity_violations(_MODULE.read_text(encoding="utf-8")) == []


def test_args_module_imports_no_io_or_clock_and_never_calls_itself() -> None:
    """task-2623로 리졸버들을 `builtins_strategy_args.py`로 분리했다 -- 그
    파일도 같은 순수성 계약(DoD (e))을 진다."""
    from src.core.script.runtime import builtins_strategy_args

    args_module = Path(builtins_strategy_args.__file__)
    assert _purity_violations(args_module.read_text(encoding="utf-8")) == []


def test_purity_gate_flags_injected_violation() -> None:
    """게이트 적색 재현: 위 테스트가 실제 모듈에서만 공허하게 통과하는 게
    아니라, 진짜 위반이 있으면 실제로 잡아낸다는 것을 증명한다(같은
    `_purity_violations` 판정 로직에 오염된 샘플 소스를 주입)."""
    tainted_source = "import os\n\n\ndef cheat(n):\n    return cheat(n - 1)\n"
    violations = _purity_violations(tainted_source)
    assert any("import" in v for v in violations)
    assert any("재귀" in v for v in violations)


# ---- 실패 주입: 검증을 우회해 이미 기록된 intent가 손상된 상황을 재현 ----


def test_intents_to_bytes_fails_closed_on_a_corrupted_nan_qty() -> None:
    """실패 주입: `_resolve_qty`의 유한성 검사를 (미래의 버그로) 우회해 통과한
    것처럼, 이미 기록된 intent를 frozen dataclass 우회 경로(`object.__setattr__`)로
    직접 손상시킨다. DoD (a) 결정론 계약의 마지막 방어선인 `intents_to_bytes`가
    `allow_nan=False`로 조용히 깨진 바이트를 만들지 않고 fail-closed로
    거부하는지 검증한다."""
    _, sb = call("entry", 1, 10.0)
    corrupted = sb.intents[0]
    object.__setattr__(corrupted, "qty", float("nan"))
    with pytest.raises(ValueError):
        intents_to_bytes(sb.intents)


# ---- 수치 성능 단언: print만 하고 단언을 피하지 않는다 ----


def test_twenty_thousand_calls_and_serialization_complete_within_one_second() -> None:
    """수치 성능 단언: 실측 20,000 (call_index=19999까지) 호출 + 직렬화가
    예산 안에 끝나는지 실제로 단언한다 (실측 약 0.1초, 10배 여유)."""
    sb = StrategyBuiltins()
    site = CallSite("strategy", "entry", "float", 4)
    start = time.perf_counter()
    for _ in range(20_000):
        sb.table[("strategy", "entry")]((1, 1.0), site)
    intents_to_bytes(sb.intents)
    elapsed = time.perf_counter() - start
    assert elapsed < 1.0
    assert len(sb.intents) == 20_000
    assert sb.intents[-1].call_index == 19_999
