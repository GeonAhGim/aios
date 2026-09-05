"""L4_analytics_authoring_backtest_marketplace_v1.0.md §9.4 DSL-10 —
`compat/cond_v2_bridge.py` 전 케이스 왕복 테스트.

(1) `tests/unit/core/test_condition_evaluator.py`의 cond-v2 픽스처 전 케이스를
변환 → DSL-3 재파싱 동일 → DSL-4 타입검사 통과 → DSL-7 IR → DSL-8 실행하고,
같은 지표 값에서 기존 `ConditionEvaluator` 결과와 3값(True/False/na) 비교.
(2) 문서화된 의미 차이(Kleene vs 좌→우 단락, 첫 틱 교차)는 값을 명시 단언하고
발화(True) 동치를 별도 단언. (3) 미지 연산자·지원 밖 지표/파라미터·혼합 결합은
위치 포함 전체 거부(negative). (4) compat_map은 JSON 직렬화 가능 dict, node_hash·
script_hash 동봉, 결정론. I-10 배선 증명: 브리지 문법이 FROZEN 평가기의 원자
정규식·`extract_indicator_keys`와 동기화돼 있음을 단언한다. 지연은 print만.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest

from src.core.script.compat import (
    COMPAT_SCHEMA,
    SIGNAL_NAME,
    BridgedScript,
    CondV2BridgeError,
    bridge_cond_v2,
    canonical_expression,
    compile_cond_v2,
    node_hash,
)
from src.core.script.compat import cond_v2_bridge as bridge_mod
from src.core.script.grammar.ast import BinaryExpr, SignalDecl, program_from_dict, to_dict
from src.core.script.grammar.parser import parse
from src.core.script.ir.lower import lower_program
from src.core.script.runtime import CallSite, Series, Value, execute
from src.core.script.typing.checker import check_program
from src.core.strategy import condition_evaluator as cond_v2
from src.core.strategy.condition_evaluator import (
    ConditionEvaluator,
    IndicatorDataMissingError,
    extract_indicator_keys,
)

State = dict[str, float]
Case = tuple[str, State, State | None, bool]
REG = "r" * 64

# test_condition_evaluator.py 픽스처 전 케이스(표현식, market_state, prev, 기대값) 그대로.
FIXTURE_CASES: list[Case] = [
    ("RSI > 30", {"RSI": 31.0}, None, True),
    ("RSI > 30", {"RSI": 30.0}, None, False),
    ("RSI >= 30", {"RSI": 30.0}, None, True),
    ("RSI < 30", {"RSI": 29.0}, None, True),
    ("RSI <= 30", {"RSI": 30.0}, None, True),
    ("RSI == 30", {"RSI": 30.0}, None, True),
    ("RSI > 30 AND SMA_timeperiod20 < 46000", {"RSI": 31.0, "SMA_timeperiod20": 45000.0},
     None, True),
    ("RSI > 30 AND SMA_timeperiod20 < 44000", {"RSI": 31.0, "SMA_timeperiod20": 45000.0},
     None, False),
    ("RSI > 30 OR SMA_timeperiod20 < 46000", {"RSI": 10.0, "SMA_timeperiod20": 45000.0},
     None, True),
    ("RSI > 30 OR SMA_timeperiod20 > 46000", {"RSI": 10.0, "SMA_timeperiod20": 45000.0},
     None, False),
    ("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, None, False),
    ("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 29.0}, True),
    ("RSI CROSSES_ABOVE 30", {"RSI": 31.0}, {"RSI": 32.0}, False),
    ("RSI CROSSES_BELOW 30", {"RSI": 29.0}, None, False),
    ("RSI CROSSES_BELOW 30", {"RSI": 29.0}, {"RSI": 31.0}, True),
]  # fmt: skip
# 저장소 다른 테스트의 cond-v2 문자열(소수·3항 결합·음수 임계값) — 값은 여기서 부여.
EXTRA_CASES: list[Case] = [
    ("RSI_timeperiod14 > 70.0", {"RSI_timeperiod14": 70.5}, None, True),
    ("RSI_timeperiod14 < 30.0", {"RSI_timeperiod14": 30.0}, None, False),
    ("RSI > 1000", {"RSI": 99.0}, None, False),
    ("RSI > 30 AND SMA_timeperiod20 < 46000 AND EMA_timeperiod9 >= -1",
     {"RSI": 31.0, "SMA_timeperiod20": 45000.0, "EMA_timeperiod9": 0.0}, None, True),
    ("OBV > 0 OR ATR_timeperiod7 CROSSES_BELOW 5", {"OBV": -1.0, "ATR_timeperiod7": 4.0},
     {"OBV": -1.0, "ATR_timeperiod7": 6.0}, True),
]  # fmt: skip
ALL_CASES = FIXTURE_CASES + EXTRA_CASES


def _reference(expr: str, state: State, prev: State | None) -> bool | None:
    """cond-v2 3값: True/False, 누락 키(판단 보류 예외)는 na(None)."""
    try:
        return ConditionEvaluator().evaluate(expr, state, prev)
    except IndicatorDataMissingError:
        return None


def _run_script(b: BridgedScript, state: State, prev: State | None) -> Value:
    """봉 0 = 직전 틱, 봉 1 = 현재 틱. `ta.*`는 cond-v2와 같은 전제(지표 값은 주어진다)로
    market_state 값을 돌려주는 스텁 레지스트리 — 봉 1의 신호를 반환한다."""
    oracle: dict[tuple[str, tuple[int, ...]], Series] = {}
    for node in b.compat_map["nodes"].values():
        if node["kind"] != "atom":
            continue
        key, call = node["key"], node["call"]
        lookup = (call["ident"], tuple(call["args"]))
        oracle[lookup] = Series.of_floats([(prev or {}).get(key), state.get(key)])

    def ta_stub(args: tuple[Value, ...], site: CallSite) -> Value:
        params = tuple(a for a in args if isinstance(a, int))
        return oracle[(site.ident, params)]

    builtins = {("ta", n["call"]["ident"]): ta_stub for n in b.compat_map["nodes"].values()
                if n["kind"] == "atom"}  # fmt: skip
    inputs = {name: Series.of_floats([1.0, 1.0]) for name in b.compat_map["inputs"]}
    result = execute(lower_program(b.program), bar_count=2, inputs=inputs, builtins=builtins)
    signal = result.signals[SIGNAL_NAME]
    assert isinstance(signal, Series)
    return signal.at(1)


# ---- (1) 전 케이스 왕복 ----


@pytest.mark.parametrize(("expr", "state", "prev", "expected"), ALL_CASES)
def test_roundtrip_same_signal_as_cond_v2(
    expr: str, state: State, prev: State | None, expected: bool
) -> None:
    ref = _reference(expr, state, prev)
    assert ref is expected, "픽스처 기대값은 FROZEN 평가기와 일치해야 한다"
    b = bridge_cond_v2(expr)
    assert parse(b.source) == b.program  # DSL-3 재파싱 동일
    assert program_from_dict(to_dict(b.program)) == b.program  # 직렬화 왕복
    env = check_program(b.program)  # DSL-4 통과
    assert env[SIGNAL_NAME] == "series<bool>"
    got = _run_script(b, state, prev)
    is_cross_first_tick = "CROSSES" in expr and any(
        (prev or {}).get(k) is None for k in extract_indicator_keys(expr)
    )
    if is_cross_first_tick:
        assert got is None and ref is False  # 문서화된 차이: 첫 틱 교차 False vs na
    else:
        assert got is ref
    assert (got is True) == (ref is True)  # 발화 동치는 무조건


def test_all_fixture_cases_are_covered_and_latency_printed() -> None:
    started = time.perf_counter()
    for expr, _, _, _ in ALL_CASES:
        bridge_cond_v2(expr)
    elapsed_ms = (time.perf_counter() - started) * 1000
    print(f"cond_v2_bridge: {len(ALL_CASES)} cases, {elapsed_ms:.1f}ms")  # noqa: T201
    assert len(FIXTURE_CASES) == 15 and len(ALL_CASES) >= 20


def test_spec_example_source_text() -> None:
    b = bridge_cond_v2("RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100")
    assert b.source.splitlines() == [
        "input close: series<float> = 0",
        "let RSI_timeperiod14 = ta.rsi(close, 14)",
        "let SMA_timeperiod20 = ta.sma(close, 20)",
        "signal cond = RSI_timeperiod14 < 30 and SMA_timeperiod20 > 100",
    ]
    sig = b.program.decls[-1]
    assert isinstance(sig, SignalDecl) and isinstance(sig.expr, BinaryExpr)
    assert sig.expr.op == "and"


def test_defaults_filled_and_multi_input_indicator() -> None:
    b = bridge_cond_v2("RSI > 30 AND ATR CROSSES_ABOVE 5 AND RSI < 70")
    lines = b.source.splitlines()
    assert lines[:3] == [
        "input high: series<float> = 0",
        "input low: series<float> = 0",
        "input close: series<float> = 0",
    ]
    assert "let RSI = ta.rsi(close, 14)" in lines
    assert "let ATR = ta.atr(high, low, close, 14)" in lines
    assert lines.count("let RSI = ta.rsi(close, 14)") == 1  # 같은 키 중복 선언 없음
    check_program(b.program)


# ---- (2) 문서화된 의미 차이 ----


def test_kleene_vs_short_circuit_divergence_is_non_firing_on_both_sides() -> None:
    expr = "RSI > 30 AND SMA_timeperiod20 < 44000"
    state = {"SMA_timeperiod20": 45000.0}  # RSI 누락, SMA는 결정적 False
    assert _reference(expr, state, None) is None  # cond-v2: 첫 조각에서 판단 보류
    got = _run_script(bridge_cond_v2(expr), state, None)
    assert got is False  # DSL Kleene: na and False = False
    assert got is not True


def test_all_missing_is_na_on_both_sides() -> None:
    for expr in ("RSI > 30", "RSI > 30 OR SMA_timeperiod20 < 1", "RSI CROSSES_ABOVE 30"):
        assert _reference(expr, {}, None) is None
        assert _run_script(bridge_cond_v2(expr), {}, None) is None


# ---- (3) negative: 전체 거부 + 위치 ----


@pytest.mark.parametrize(
    ("expr", "code", "part"),
    [
        ("RSI ~ 30", "STRATEGY_CONDITION_SYNTAX", 0),
        ("this is not valid", "STRATEGY_CONDITION_SYNTAX", 0),
        ("", "STRATEGY_CONDITION_SYNTAX", 0),
        ("RSI > 30 AND SMA_timeperiod20 < 1 OR RSI < 5", "STRATEGY_CONDITION_SYNTAX", 1),
        ("RSI > 30 AND rsi_x > 1", "STRATEGY_CONDITION_SYNTAX", 1),
        ("PRICE > 10000", "STRATEGY_INDICATOR_UNKNOWN", 0),
        ("RSI > 30 OR FOO > 1", "STRATEGY_INDICATOR_UNKNOWN", 1),
        ("RSI_foo14 > 1", "STRATEGY_PARAM_OUT_OF_RANGE", 0),
        ("RSI_timeperiod1 > 1", "STRATEGY_PARAM_OUT_OF_RANGE", 0),
        ("MACD > 0", "SCRIPT_COMPAT_UNSUPPORTED", 0),
        ("RSI > 30 AND BBANDS_timeperiod5 > 0", "SCRIPT_COMPAT_UNSUPPORTED", 1),
    ],
)
def test_rejects_whole_expression_with_position(expr: str, code: str, part: int) -> None:
    with pytest.raises(CondV2BridgeError) as info:
        bridge_cond_v2(expr)
    err = info.value
    assert err.code == code and err.part_index == part
    joiner = " AND " if " AND " in expr else " OR "
    assert err.offset == sum(len(p) + len(joiner) for p in expr.split(joiner)[:part])
    assert f"offset {err.offset}" in str(err)


def test_second_part_offset_points_into_original_string() -> None:
    expr = "RSI > 30 AND FOO > 1"
    with pytest.raises(CondV2BridgeError) as info:
        bridge_cond_v2(expr)
    assert expr[info.value.offset :] == "FOO > 1"


# ---- (4) compat_map: dict 필드, 해시, 결정론 ----


def test_compat_map_is_json_dict_with_node_and_script_hash() -> None:
    expr = "RSI_timeperiod14 < 30 AND SMA_timeperiod20 > 100"
    out = compile_cond_v2(expr, registry_version=REG)
    cm = out.compat_map
    assert json.loads(json.dumps(cm)) == cm
    assert cm["schema"] == COMPAT_SCHEMA and cm["signal"] == SIGNAL_NAME
    assert cm["source_grammar_version"] == "cond-v2"
    assert cm["target_grammar_version"] == out.compiled.grammar_version
    assert cm["script_hash"] == out.compiled.script_hash and len(cm["script_hash"]) == 64
    assert cm["node_hash"] == hashlib.sha256(expr.encode()).hexdigest() == node_hash(expr)
    assert cm["inputs"] == ["close"]
    assert cm["nodes"]["root"] == {"kind": "root", "op": "and", "line": 4, "ident": SIGNAL_NAME}
    atom1 = cm["nodes"]["atom:1"]
    assert atom1["key"] == "SMA_timeperiod20" and atom1["ident"] == atom1["key"]
    assert atom1["line"] == 3 and atom1["offset"] == expr.index("SMA")
    assert atom1["op"] == ">" and atom1["threshold"] == "100"
    assert atom1["call"] == {"ns": "ta", "ident": "sma", "args": [20]}
    assert bridge_cond_v2(expr).compat_map["script_hash"] is None  # 컴파일 전에는 미정


def test_node_hash_normalizes_whitespace_and_preserves_leaf_order() -> None:
    a, b = "RSI > 30 AND  SMA_timeperiod20 <   100", "RSI > 30 AND SMA_timeperiod20 < 100"
    assert canonical_expression(a) == b and node_hash(a) == node_hash(b)
    assert node_hash("RSI > 30 AND SMA_timeperiod20 < 100") != node_hash(
        "SMA_timeperiod20 < 100 AND RSI > 30"
    )


def test_bridge_is_deterministic() -> None:
    expr = "RSI > 30 OR SMA_timeperiod20 < 46000"
    first = compile_cond_v2(expr, registry_version=REG)
    second = compile_cond_v2(expr, registry_version=REG)
    assert first.compat_map == second.compat_map
    assert first.compiled.ir_bytes == second.compiled.ir_bytes


# ---- I-10 배선 증명: FROZEN 평가기 문법과의 동기화 ----


def test_bridge_grammar_is_synced_with_frozen_evaluator() -> None:
    assert bridge_mod._ATOMIC_RE.pattern == cond_v2._ATOMIC_RE.pattern
    for expr, _, _, _ in ALL_CASES:
        nodes = bridge_cond_v2(expr).compat_map["nodes"]
        keys = [n["key"] for k, n in nodes.items() if k != "root"]
        assert keys == extract_indicator_keys(expr)


def test_bridge_does_not_modify_frozen_strategy_package() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(bridge_mod))
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any(m.startswith("src.core.strategy") for m in imported)
    assert not any(m.startswith(("src.db", "src.services", "sqlalchemy")) for m in imported)
