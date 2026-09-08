"""L02 — 지표 조회·파라미터 검증·lookback·registry_hash 단일 진입점.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.2 L02

순수 모듈 — I/O 없음, L01(`spec`, `specs_talib`)만 소비하고 지표 계산은
하지 않는다. `registry_hash()`는 `strategy_artifact.registry_version`의
입력이 되므로(§7 아티팩트 해시 규칙) dict 순서·부동소수에 의존하지 않게
스펙 이름으로 정렬한 뒤 `sort_keys` JSON으로 정준 직렬화한다. `lookback`
콜러블 자체(함수 객체)는 해시에 넣을 수 없으므로 `__name__`으로 대신한다 —
동일 모듈에서 재기동해도 같은 이름이 나오므로 프로세스 재기동에 안정적이다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass

from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS


def canonical_spec_dict(name: str, spec: IndicatorSpec) -> dict[str, object]:
    """스펙 하나를 JSON 직렬화 가능한 정준 형태로 만든다(`registry_hash` 및
    IND-10 생성 결정론 테스트 공용 — `lookback` 콜러블 자체는 해시에 못 넣으므로
    `__name__`으로 대신한다)."""
    return {
        "name": name,
        "inputs": list(spec.inputs),
        "params": [
            {
                "name": param_spec.name,
                "min": param_spec.min,
                "max": param_spec.max,
                "default": param_spec.default,
            }
            for param_spec in spec.params
        ],
        "outputs": list(spec.outputs),
        "causal": spec.causal,
        "lookback": spec.lookback.__name__,
        "plots": [
            {
                "kind": plot.kind,
                "scale": plot.scale,
                "default_pane": plot.default_pane,
                "fill_between": plot.fill_between,
                "color_rule": plot.color_rule,
                "precision": plot.precision,
                "legend_format": plot.legend_format,
            }
            for plot in spec.plots
        ],
    }


class IndicatorError(Exception):
    """레지스트리 조회/검증 실패. `code`는 API 계층이 400 매핑에 쓴다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class IndicatorRegistry:
    """지표 스펙 조회·파라미터 검증·lookback 산출 단일 진입점."""

    def __init__(self, specs: Mapping[str, IndicatorSpec] | None = None) -> None:
        self._specs: dict[str, IndicatorSpec] = (
            dict(specs) if specs is not None else dict(TALIB_SPECS)
        )

    def get(self, name: str) -> IndicatorSpec:
        try:
            return self._specs[name]
        except KeyError:
            raise IndicatorError("STRATEGY_INDICATOR_UNKNOWN") from None

    def validate_params(self, name: str, params: Mapping[str, int]) -> dict[str, int]:
        spec = self.get(name)
        resolved: dict[str, int] = {}
        for param_spec in spec.params:
            value = params.get(param_spec.name, param_spec.default)
            if not isinstance(value, int) or isinstance(value, bool):
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            if not (param_spec.min <= value <= param_spec.max):
                raise IndicatorError("STRATEGY_PARAM_OUT_OF_RANGE")
            resolved[param_spec.name] = value
        return resolved

    def lookback(self, name: str, params: Mapping[str, int]) -> int:
        spec = self.get(name)
        resolved = self.validate_params(name, params)
        return spec.lookback(resolved)

    def registry_hash(self) -> str:
        canonical = [canonical_spec_dict(name, spec) for name, spec in sorted(self._specs.items())]
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DEFAULT_REGISTRY = IndicatorRegistry()


# --- IND-16: indicator-on-indicator dependency graph ------------------------
#
# Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.11 IND-16.
# An input to one indicator may be another indicator's output instead of a raw
# market-data column. This section only builds and validates the dependency
# graph (cycle detection, depth cap, composite lookback) on top of the
# `IndicatorRegistry` methods above — it does not compute anything itself;
# `engine/__init__.py` delegates the actual math to `engine/vectorized.py` /
# `engine/incremental.py` per node (no formula reimplementation, IND-1 SSOT).
#
# Error codes added here:
# - `INDICATOR_CHAIN_CYCLE` — a node (transitively, or by self-reference)
#   depends on its own output.
# - `INDICATOR_CHAIN_TOO_DEEP` — chain depth exceeds `MAX_CHAIN_DEPTH`
#   (fail-closed guard against unbounded recursion).
# `INDICATOR_INPUT_INVALID` is reused for an unknown node reference, an input
# name that doesn't match the indicator's declared `spec.inputs`, or an
# `output` name that doesn't match the referenced node's `spec.outputs`.

MAX_CHAIN_DEPTH = 8


@dataclass(frozen=True)
class ColumnSource:
    """A chain node input backed by a raw base market-data column (e.g. "close")."""

    column: str


@dataclass(frozen=True)
class NodeSource:
    """A chain node input backed by another chain node's output."""

    node: str
    output: str


ChainInput = ColumnSource | NodeSource


@dataclass(frozen=True)
class ChainNode:
    """One node of a dependency graph: an indicator instance (`name`+`params`,
    the L02 lookup key) plus a `ChainInput` for every name in that
    indicator's declared `spec.inputs`."""

    name: str
    params: Mapping[str, int]
    inputs: Mapping[str, ChainInput]


ChainGraph = Mapping[str, ChainNode]


def resolve_chain(
    graph: ChainGraph, root: str, registry: IndicatorRegistry
) -> tuple[list[str], int]:
    """Validate `graph` fail-closed and return (`root`'s dependencies-first
    compute order, composite lookback for `root`).

    Composite lookback per node is its own lookback plus the *maximum* of its
    parent nodes' composite lookbacks (parents share the same time axis, so
    they don't stack additively) — for a single linear chain this maximum
    degenerates to a sum, matching §9.11 DoD (c).
    """
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    lookback_of: dict[str, int] = {}

    def visit(node_id: str, depth: int) -> None:
        if node_id in visited:
            return
        if node_id in visiting:
            raise IndicatorError("INDICATOR_CHAIN_CYCLE")
        if node_id not in graph:
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        if depth > MAX_CHAIN_DEPTH:
            raise IndicatorError("INDICATOR_CHAIN_TOO_DEEP")
        node = graph[node_id]
        spec = registry.get(node.name)
        if set(node.inputs) != set(spec.inputs):
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        visiting.add(node_id)
        parent_lookbacks = [0]
        for source in node.inputs.values():
            if isinstance(source, ColumnSource):
                continue
            visit(source.node, depth + 1)
            parent_spec = registry.get(graph[source.node].name)
            if source.output not in parent_spec.outputs:
                raise IndicatorError("INDICATOR_INPUT_INVALID")
            parent_lookbacks.append(lookback_of[source.node])
        visiting.discard(node_id)
        visited.add(node_id)
        order.append(node_id)
        lookback_of[node_id] = registry.lookback(node.name, node.params) + max(parent_lookbacks)

    visit(root, 1)
    return order, lookback_of[root]
