"""IND-1 — 지표 엔진 공통 계약(증분 `incremental.py` · 일괄 `vectorized.py`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.3, §9.3 IND-1

두 엔진이 공유하는 요청 해석과 입력 검증만 둔다. 지표 조회·파라미터 범위·
lookback은 L02 `IndicatorRegistry`(L01 TA-Lib 실측 lookback) 단일 출처에 위임하고
여기서 재정의하지 않는다. 오류 코드:
- `STRATEGY_INDICATOR_UNKNOWN` / `STRATEGY_PARAM_OUT_OF_RANGE` — L02 그대로.
- `INDICATOR_INPUT_INVALID` — 입력 컬럼 누락·길이 불일치·비유한값(fail-closed).
- `INDICATOR_LOOKBACK_MISMATCH` — 엔진 산출의 NaN 접두 길이가 레지스트리 lookback과 다름.
- `INDICATOR_ENGINE_MISMATCH` — 증분과 일괄 결과가 1e-9 밖(`vectorized.check_equivalence`).

IND-16 (indicator-on-indicator) adds `compute_chain`/`run_chain_incremental`
below. They wire a `registry.ChainGraph`'s nodes together and delegate every
node's math to `engine/vectorized.compute` or `engine/incremental.py`'s
`IncrementalIndicator` (imported lazily inside the functions — both of those
modules import this module at load time, so a module-level import here would
be circular). New error code:
- `INDICATOR_CHAIN_LOOKBACK_INSUFFICIENT` — fewer bars than the chain's
  composite lookback (`registry.resolve_chain`); fail-closed, no silent
  zero/NaN result.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.indicators.registry import (
    DEFAULT_REGISTRY,
    ChainGraph,
    ColumnSource,
    IndicatorError,
    IndicatorRegistry,
    resolve_chain,
)
from src.core.indicators.spec import IndicatorSpec

__all__ = [
    "Bar",
    "Values",
    "compute_chain",
    "resolve_request",
    "run_chain_incremental",
    "validate_input",
]

Values = tuple[float, ...]
Bar = Mapping[str, float | int | Decimal]
FloatArray = np.ndarray[Any, np.dtype[np.float64]]
BaseColumns = Mapping[str, Sequence[float | int | Decimal] | FloatArray]


def resolve_request(
    name: str, params: Mapping[str, int] | None, registry: IndicatorRegistry
) -> tuple[IndicatorSpec, dict[str, int], int]:
    """(spec, 검증된 params, 레지스트리 lookback).

    MACD `fastperiod >= slowperiod`는 L01 lookback 산식(slow 기준)과 실제 필요
    bar 수가 어긋나므로 fail-closed 거부한다(TA-Lib은 두 기간을 맞바꾸지만 그
    경우 lookback이 레지스트리 값과 달라진다).
    """
    spec = registry.get(name)
    resolved = registry.validate_params(name, params or {})
    if name == "MACD" and resolved["fastperiod"] >= resolved["slowperiod"]:
        raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
    return spec, resolved, registry.lookback(name, resolved)


def _finite(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    number = float(value)
    if not math.isfinite(number):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return number


def validate_input(spec: IndicatorSpec, bar: Bar) -> dict[str, float]:
    """spec.inputs 전부가 유한한 수로 존재해야 통과. 누락·NaN·inf·bool은 거부."""
    inputs = {key: _finite(bar[key]) for key in spec.inputs if key in bar}
    if len(inputs) != len(spec.inputs):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return inputs


def compute_chain(
    graph: ChainGraph,
    root: str,
    base_columns: BaseColumns,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> dict[str, FloatArray]:
    """Batch-compute a chain (IND-16): topologically run every node through
    `vectorized.compute`, feeding each node's output as a downstream node's
    input column. Before delegating a node, its inputs are sliced from the
    max of its parents' NaN-prefix lengths so `vectorized._as_columns`'s
    "every value finite" invariant still holds for chained inputs; the
    result is then left-padded back to the original length so the returned
    arrays stay indexed against the original bars.
    """
    from src.core.indicators.engine.vectorized import compute as _compute

    order, lookback = resolve_chain(graph, root, registry)
    n = len(next(iter(base_columns.values())))
    if n < lookback:
        raise IndicatorError("INDICATOR_CHAIN_LOOKBACK_INSUFFICIENT")

    prefix_of: dict[str, int] = {}
    outputs_of: dict[str, dict[str, FloatArray]] = {}
    for node_id in order:
        node = graph[node_id]
        parent_prefix = 0
        columns: dict[str, Any] = {}
        for input_name, source in node.inputs.items():
            if isinstance(source, ColumnSource):
                columns[input_name] = np.asarray(base_columns[source.column], dtype=np.float64)
            else:
                parent_prefix = max(parent_prefix, prefix_of[source.node])
                columns[input_name] = outputs_of[source.node][source.output]
        sliced = {key: value[parent_prefix:] for key, value in columns.items()}
        result = _compute(node.name, sliced, node.params, registry)
        prefix_of[node_id] = parent_prefix + registry.lookback(node.name, node.params)
        outputs_of[node_id] = {
            out_name: np.concatenate([np.full(parent_prefix, np.nan), arr])
            for out_name, arr in result.items()
        }
    return outputs_of[root]


def run_chain_incremental(
    graph: ChainGraph,
    root: str,
    base_columns: BaseColumns,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> dict[str, FloatArray]:
    """Streaming counterpart of `compute_chain`, used to prove the IND-1
    incremental==batch equivalence property also holds for chains (§9.11
    (e)). Each node is a plain `IncrementalIndicator`; a downstream node is
    only fed a bar once its upstream node has produced a value for that bar
    (never a substituted zero — same fail-closed rule as the single-node
    engines).
    """
    from src.core.indicators.engine.incremental import IncrementalIndicator

    order, lookback = resolve_chain(graph, root, registry)
    columns = {key: np.asarray(value, dtype=np.float64) for key, value in base_columns.items()}
    n = len(next(iter(columns.values())))
    if n < lookback:
        raise IndicatorError("INDICATOR_CHAIN_LOOKBACK_INSUFFICIENT")

    states = {
        node_id: IncrementalIndicator(graph[node_id].name, graph[node_id].params, registry)
        for node_id in order
    }
    values_at: dict[str, dict[str, list[float | None]]] = {
        node_id: {out: [None] * n for out in states[node_id].spec.outputs} for node_id in order
    }
    for i in range(n):
        for node_id in order:
            node = graph[node_id]
            bar: dict[str, float] = {}
            ready = True
            for input_name, source in node.inputs.items():
                if isinstance(source, ColumnSource):
                    bar[input_name] = float(columns[source.column][i])
                    continue
                value = values_at[source.node][source.output][i]
                if value is None:
                    ready = False
                    break
                bar[input_name] = value
            if not ready:
                continue
            for out_name, value in states[node_id].update(bar).items():
                values_at[node_id][out_name][i] = value

    collected: dict[str, FloatArray] = {}
    for out_name, series in values_at[root].items():
        arr = np.full(n, np.nan)
        for i, value in enumerate(series):
            if value is not None:
                arr[i] = value
        collected[out_name] = arr
    return collected
