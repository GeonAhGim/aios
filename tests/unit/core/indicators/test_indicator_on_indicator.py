"""IND-16 — indicator-on-indicator(지표 입력으로 다른 지표 출력) 계약 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.11 IND-16.
DoD (전부 반증 가능): (a) RSI(SMA(close,20),14) 정확값, (b) 순환·자기참조 거부,
(c) lookback 체인 합성 + fail-closed, (d) 깊이 상한, (e) 증분=일괄(1e-9),
(f) 기존 카탈로그/레지스트리 계약 무변경.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from src.core.indicators.catalog.registry_tiers import DEFAULT_STATIC_CATALOG, chain_entry_hash
from src.core.indicators.engine import compute_chain, run_chain_incremental
from src.core.indicators.engine.vectorized import EQUIVALENCE_TOLERANCE, compute
from src.core.indicators.registry import (
    DEFAULT_REGISTRY,
    MAX_CHAIN_DEPTH,
    ChainNode,
    ColumnSource,
    IndicatorError,
    NodeSource,
    resolve_chain,
)
from src.core.indicators.specs_talib import TALIB_SPECS


def _close(seed: int, n: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(size=n))
    return np.maximum(close, 1.0)


def _rsi_of_sma_graph() -> dict[str, ChainNode]:
    return {
        "sma": ChainNode(
            name="SMA", params={"timeperiod": 20}, inputs={"close": ColumnSource("close")}
        ),
        "rsi": ChainNode(
            name="RSI",
            params={"timeperiod": 14},
            inputs={"close": NodeSource(node="sma", output="value")},
        ),
    }


# --- (a) 정확값: RSI(SMA(close,20),14) -------------------------------------


def test_chain_matches_hand_built_reference_and_differs_from_unchained() -> None:
    close = _close(1, 200)
    graph = _rsi_of_sma_graph()

    sma = compute("SMA", {"close": close}, {"timeperiod": 20})["value"]
    sma_valid = sma[19:]  # strip SMA's own lookback prefix before feeding RSI
    reference = compute("RSI", {"close": sma_valid}, {"timeperiod": 14})["value"]

    chained = compute_chain(graph, "rsi", {"close": close})["value"]
    assert np.isnan(chained[:33]).all()
    np.testing.assert_allclose(chained[33:], reference[14:], rtol=0, atol=1e-9)

    plain_rsi = compute("RSI", {"close": close}, {"timeperiod": 14})["value"]
    assert not np.allclose(chained[33:], plain_rsi[33:], atol=1e-9)


# --- (b) 순환 거부 -----------------------------------------------------------


def test_two_node_cycle_is_rejected() -> None:
    input_a = {"close": NodeSource(node="b", output="value")}
    input_b = {"close": NodeSource(node="a", output="value")}
    graph = {
        "a": ChainNode(name="SMA", params={}, inputs=input_a),
        "b": ChainNode(name="SMA", params={}, inputs=input_b),
    }
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "a", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_CHAIN_CYCLE"


def test_self_reference_is_rejected_with_same_code_as_cycle() -> None:
    self_input = {"close": NodeSource(node="a", output="value")}
    graph = {
        "a": ChainNode(name="SMA", params={}, inputs=self_input),
    }
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "a", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_CHAIN_CYCLE"


# --- (c) lookback 합성 + fail-closed ----------------------------------------


def test_composite_lookback_is_sum_for_a_linear_chain() -> None:
    graph = _rsi_of_sma_graph()
    order, lookback = resolve_chain(graph, "rsi", DEFAULT_REGISTRY)
    assert order == ["sma", "rsi"]
    sma_lookback = DEFAULT_REGISTRY.lookback("SMA", {"timeperiod": 20})
    rsi_lookback = DEFAULT_REGISTRY.lookback("RSI", {"timeperiod": 14})
    assert lookback == sma_lookback + rsi_lookback == 33


@pytest.mark.parametrize("engine_fn", [compute_chain, run_chain_incremental])
def test_series_shorter_than_composite_lookback_is_rejected_not_silent(engine_fn) -> None:
    close = _close(2, 32)  # composite lookback is 33 -- one bar short
    graph = _rsi_of_sma_graph()
    with pytest.raises(IndicatorError) as excinfo:
        engine_fn(graph, "rsi", {"close": close}, DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_CHAIN_LOOKBACK_INSUFFICIENT"


# --- (d) 깊이 상한 -----------------------------------------------------------


def _linear_sma_chain(depth: int) -> dict[str, ChainNode]:
    root_input = {"close": ColumnSource("close")}
    graph: dict[str, ChainNode] = {
        "n0": ChainNode(name="SMA", params={"timeperiod": 2}, inputs=root_input)
    }
    for i in range(1, depth):
        graph[f"n{i}"] = ChainNode(
            name="SMA",
            params={"timeperiod": 2},
            inputs={"close": NodeSource(node=f"n{i - 1}", output="value")},
        )
    return graph


def test_chain_deeper_than_cap_is_rejected() -> None:
    depth = MAX_CHAIN_DEPTH + 4
    graph = _linear_sma_chain(depth)
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, f"n{depth - 1}", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_CHAIN_TOO_DEEP"


def test_chain_within_cap_is_accepted() -> None:
    depth = MAX_CHAIN_DEPTH - 1
    graph = _linear_sma_chain(depth)
    order, _ = resolve_chain(graph, f"n{depth - 1}", DEFAULT_REGISTRY)
    assert order[0] == "n0"


# --- other fail-closed input validation -------------------------------------


def test_unknown_node_reference_is_rejected() -> None:
    graph = {
        "rsi": ChainNode(
            name="RSI", params={}, inputs={"close": NodeSource(node="missing", output="value")}
        ),
    }
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "rsi", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_INPUT_INVALID"


def test_missing_declared_input_is_rejected() -> None:
    graph = {"atr": ChainNode(name="ATR", params={}, inputs={"close": ColumnSource("close")})}
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "atr", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_INPUT_INVALID"


def test_unknown_parent_output_name_is_rejected() -> None:
    graph = {
        "sma": ChainNode(name="SMA", params={}, inputs={"close": ColumnSource("close")}),
        "rsi": ChainNode(
            name="RSI", params={}, inputs={"close": NodeSource(node="sma", output="bogus")}
        ),
    }
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "rsi", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_INPUT_INVALID"


# --- (e) 증분 == 일괄 (1e-9) -------------------------------------------------


@pytest.mark.parametrize("seed", range(5))
def test_incremental_chain_equals_batch_chain_within_1e9(seed: int) -> None:
    rng = np.random.default_rng(seed * 97 + 3)
    n = 33 + int(rng.integers(1, 150))
    close = _close(seed, n)
    graph = _rsi_of_sma_graph()

    batch = compute_chain(graph, "rsi", {"close": close})
    streamed = run_chain_incremental(graph, "rsi", {"close": close})
    for key, a in batch.items():
        b = streamed[key]
        assert np.array_equal(np.isnan(a), np.isnan(b)), key
        finite = ~np.isnan(a)
        scale = np.maximum(1.0, np.maximum(np.abs(a[finite]), np.abs(b[finite])))
        assert np.max(np.abs(a[finite] - b[finite]) / scale) <= EQUIVALENCE_TOLERANCE


# --- (f) 기존 카탈로그/레지스트리 계약 무변경 --------------------------------


def test_default_static_catalog_and_registry_hash_unaffected_by_ind16() -> None:
    assert len(DEFAULT_STATIC_CATALOG) == len(TALIB_SPECS)
    baseline = DEFAULT_REGISTRY.registry_hash()
    resolve_chain(_rsi_of_sma_graph(), "rsi", DEFAULT_REGISTRY)
    assert DEFAULT_REGISTRY.registry_hash() == baseline


def test_chain_entry_hash_is_deterministic_and_sensitive_to_graph_shape() -> None:
    graph = _rsi_of_sma_graph()
    first = chain_entry_hash("rsi", graph, DEFAULT_REGISTRY)
    second = chain_entry_hash("rsi", graph, DEFAULT_REGISTRY)
    assert first == second

    changed = dict(graph)
    changed["sma"] = ChainNode(
        name="SMA", params={"timeperiod": 21}, inputs={"close": ColumnSource("close")}
    )
    assert chain_entry_hash("rsi", changed, DEFAULT_REGISTRY) != first


# --- DEEPEN task-2930: 실패 주입 + 수치 성능 단언 + 게이트 적색 재현 ---------
#
# task-2727 DEPTH 감사(docs/audit/DEPTH_DSL_IND.md)에서 원 task-2141(IND-16)이
# 축 하한 미달로 판정됐다. negative(위 7건)는 이미 충분하므로, 부족했던 세
# 증빙만 추가한다: 실패 주입(fault injection), 수치 성능 단언, 게이트 적색
# 재현(회귀 가드) 각 1건.


def test_non_finite_value_in_base_column_fails_closed_not_silently_propagated() -> None:
    """실패 주입: `close`에 심은 `inf` 하나가 체인의 부모 노드(SMA) 계산에서
    거부돼야 한다 -- IND-1 `vectorized._as_columns`의 `isfinite` 불변식이
    실제로 체인 경로에도 배선돼 있다는 증거. 이 검사가 깨지면 inf가 조용히
    다운스트림 RSI 계산에 섞여 들어가 버그 없이 통과해버린다."""
    close = _close(3, 200)
    close[100] = np.inf
    graph = _rsi_of_sma_graph()
    with pytest.raises(IndicatorError) as excinfo:
        compute_chain(graph, "rsi", {"close": close})
    assert excinfo.value.code == "INDICATOR_INPUT_INVALID"


def test_resolve_chain_latency_is_bounded_for_max_depth_chain() -> None:
    """수치 성능 단언: 그래프 해석(`resolve_chain`의 위상 정렬)과 lookback
    체인 합성은 깊이 상한(MAX_CHAIN_DEPTH) 그래프에서도 호출당 5ms를 넘지
    않아야 한다. O(depth) DFS가 실수로 재귀 재계산형(예: 메모이즈 누락으로
    지수적 재방문)으로 퇴화하면 이 경계가 깨진다."""
    graph = _linear_sma_chain(MAX_CHAIN_DEPTH)
    root = f"n{MAX_CHAIN_DEPTH - 1}"
    resolve_chain(graph, root, DEFAULT_REGISTRY)  # warm-up: import/캐시 워밍업 제외

    iterations = 500
    start = time.perf_counter()
    for _ in range(iterations):
        resolve_chain(graph, root, DEFAULT_REGISTRY)
    elapsed = time.perf_counter() - start

    per_call_ms = (elapsed / iterations) * 1000
    assert per_call_ms < 5.0, f"resolve_chain graph 해석 지연 회귀: {per_call_ms:.4f}ms/call"


def test_cycle_hidden_behind_one_valid_sibling_branch_is_still_detected() -> None:
    """게이트 적색 재현(순환 탐지 회귀 가드): 다중 입력 노드(ATR)의 한 입력
    (`high`)은 순환이 없는 정상 분기라 먼저 방문·완료되어 `visited`에
    캐시된다. 같은 노드의 다른 입력(`close`)만 자기 자신으로 되돌아오는
    순환이다 -- "형제 입력 하나가 정상 완료되면 노드 전체를 안전하다고
    캐시한다"는 회귀가 생기면(예: `visiting`을 노드 단위가 아니라 첫 입력
    처리 후 조기 discard) 이 테스트가 적색이 된다. 위 (b)의 2노드 순환
    테스트는 단일 입력 체인만 다뤄 이 회귀를 못 잡는다."""
    graph = {
        "base_h": ChainNode(
            name="SMA", params={"timeperiod": 2}, inputs={"close": ColumnSource("high")}
        ),
        "mix": ChainNode(
            name="ATR",
            params={"timeperiod": 14},
            inputs={
                "high": NodeSource(node="base_h", output="value"),
                "low": ColumnSource("low"),
                "close": NodeSource(node="base_c", output="value"),
            },
        ),
        "base_c": ChainNode(
            name="SMA",
            params={"timeperiod": 2},
            inputs={"close": NodeSource(node="mix", output="value")},
        ),
    }
    with pytest.raises(IndicatorError) as excinfo:
        resolve_chain(graph, "mix", DEFAULT_REGISTRY)
    assert excinfo.value.code == "INDICATOR_CHAIN_CYCLE"
