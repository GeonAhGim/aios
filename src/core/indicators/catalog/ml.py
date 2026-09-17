"""AI-21 -- catalog/ml.py: register `ml.<model_id>` indicators (IND spec
compliance) + prove incremental=batch equivalence for the ML compute path.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.5 AI-21
(`src/core/indicators/catalog/ml.py`: "`ml.<model_id>` indicator registered
with the registry (IND spec compliance)"), §9 AI-21 DoD.

Unlike `catalog/registry_tiers.py`'s CORE/OSS/SCRIPT tiers, an
`ml.<model_id>` indicator's math is not a fixed TA-Lib formula living in
`engine/vectorized.py`'s `_KERNELS` / `engine/incremental.py`'s `_STATES`
closed dicts (those modules' own docstrings: only 11 of TALIB_SPECS' 161
indicators are hand-implemented there, IND-1 scope). It is whatever model a
caller has trained (AI-19/20) -- the model, not a formula, is the kernel.

This module takes that model as a plain `MlScorer` callable
(`Mapping[str, float] -> float`) rather than importing `src.foundation.ml`'s
`ModelCard`/`ModelPredictorPort`: `.importlinter`'s `forbidden:core-no-io`
contract (RATCHET-2, `scripts/check_import_linter.py`) forbids `src/core`
from importing `src/foundation` at all (`import-linter-baseline.json`
`"core-no-io": 33` is a ratchet ceiling, not a budget to spend). The caller
that owns a `ModelCard`/`ModelPredictorPort` (e.g. a future
`src/foundation/ai/factory` or `src/api` wiring point) partially applies
`predictor.predict` against a fixed `card` to get an `MlScorer` before
calling into this module -- keeping the dependency arrow foundation ->
core, matching every existing indicator caller (`ta.*` builtins,
`ai/factory/application/research_tools.py`, both depend on `core/
indicators`, never the reverse).

This module supplies:
1. `build_ml_indicator_spec` -- the L01 `IndicatorSpec`/`PlotSpec`
   registration contract (proven against the real `IndicatorRegistry`/L02
   in the test suite, not just structurally resembled).
2. A memoryless batch/incremental compute pair (`compute_ml_signal` /
   `run_ml_signal_incremental`) plus `check_ml_signal_equivalence`, the
   catalog-layer counterpart of `application/serve_signal.py::
   check_signal_equivalence` one layer up -- IND-1's incremental=batch
   equivalence (1e-9) contract applies here too even though there is no
   rolling window: a model scores each bar from that bar's own feature
   columns only, so `lookback=0` and the two compute paths are the same
   per-row loop called
   through two different entry points (mirrors `engine/vectorized.py` vs.
   `engine/incremental.py`'s split so a future refactor that adds caching
   to one path and not the other gets caught here instead of live).

This module never imports `engine/vectorized.py` or `engine/incremental.py`
-- `ml.<model_id>` names are not registered in those modules' `_KERNELS`/
`_STATES` dicts, so routing a lookup through `IncrementalIndicator`/
`vectorized.compute` would raise `STRATEGY_INDICATOR_UNKNOWN` rather than
score anything.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

import numpy as np

from src.core.indicators.registry import IndicatorError
from src.core.indicators.spec import IndicatorSpec, PlotSpec

__all__ = [
    "FloatArray",
    "Columns",
    "MlScorer",
    "EQUIVALENCE_TOLERANCE",
    "ml_indicator_name",
    "build_ml_indicator_spec",
    "compute_ml_signal",
    "run_ml_signal_incremental",
    "check_ml_signal_equivalence",
]

FloatArray = np.ndarray[Any, np.dtype[np.float64]]
Columns = Mapping[str, Sequence[float] | FloatArray]
MlScorer = Callable[[Mapping[str, float]], float]
"""One trained model's inference call, already bound to a specific
`ModelCard`/version by the caller -- pure function of the feature vector
(same "deterministic, no hidden state" contract
`src.foundation.ml.ports.predictor.ModelPredictorPort.predict` documents,
minus the `card` argument this module has no business importing)."""

EQUIVALENCE_TOLERANCE = 1e-9


def ml_indicator_name(model_id: str) -> str:
    return f"ml.{model_id}"


def build_ml_indicator_spec(model_id: str, feature_ids: Sequence[str]) -> IndicatorSpec:
    """One `ml.<model_id>` indicator spec: inputs are the model's declared
    feature columns (already-computed numeric series, same convention as a
    TA-Lib indicator's `close`/`high`/... inputs -- feature engineering
    itself is out of scope here, AI-19's feature store already produced
    these columns). Single `signal` output, `lookback=0`: the model scores
    each bar from that bar's own feature values only, so unlike SMA/RSI/...
    it is ready from the very first bar (no window to fill)."""
    if not model_id.strip():
        raise ValueError("model_id must not be empty")
    if not feature_ids:
        raise ValueError("feature_ids must not be empty")
    if len(set(feature_ids)) != len(feature_ids):
        raise ValueError(f"duplicate feature_ids: {feature_ids!r}")
    return IndicatorSpec(
        name=ml_indicator_name(model_id),
        inputs=tuple(feature_ids),
        params=(),
        outputs=("signal",),
        lookback=lambda _params: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
        causal=True,
    )


def _as_columns(spec: IndicatorSpec, columns: Columns) -> dict[str, FloatArray]:
    arrays: dict[str, FloatArray] = {}
    for key in spec.inputs:
        if key not in columns:
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        arr = np.asarray(columns[key], dtype=np.float64)
        if arr.ndim != 1 or not np.all(np.isfinite(arr)):
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        arrays[key] = arr
    if len({len(a) for a in arrays.values()}) > 1 or not len(next(iter(arrays.values()))):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return arrays


def compute_ml_signal(
    model_id: str, feature_ids: Sequence[str], columns: Columns, scorer: MlScorer
) -> FloatArray:
    """Batch path: score every row in one pass (backtest-style bulk call,
    the `engine/vectorized.compute` counterpart)."""
    spec = build_ml_indicator_spec(model_id, feature_ids)
    arrays = _as_columns(spec, columns)
    n = len(next(iter(arrays.values())))
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        features = {key: float(arrays[key][i]) for key in feature_ids}
        out[i] = scorer(features)
    return out


def run_ml_signal_incremental(
    model_id: str, feature_ids: Sequence[str], columns: Columns, scorer: MlScorer
) -> FloatArray:
    """Streaming path: score one bar at a time as it arrives (replay/live
    style, the `IncrementalIndicator` counterpart) -- same per-bar formula
    as `compute_ml_signal`, reached through a separate code path so
    `check_ml_signal_equivalence` actually proves something rather than
    comparing a function against itself."""
    spec = build_ml_indicator_spec(model_id, feature_ids)
    arrays = _as_columns(spec, columns)
    n = len(next(iter(arrays.values())))
    out = np.empty(n, dtype=np.float64)
    for i in range(n):
        bar = {key: float(arrays[key][i]) for key in feature_ids}
        out[i] = scorer(bar)
    return out


def check_ml_signal_equivalence(
    model_id: str,
    feature_ids: Sequence[str],
    columns: Columns,
    scorer: MlScorer,
    tolerance: float = EQUIVALENCE_TOLERANCE,
) -> float:
    """AI-21 DoD "incremental=batch equivalence" for the `ml.*` compute path. Raises
    `IndicatorError("INDICATOR_ENGINE_MISMATCH")` (same code IND-1's
    `vectorized.check_equivalence` uses) if the two paths disagree beyond
    `tolerance`; returns the worst scaled difference found otherwise."""
    batch = compute_ml_signal(model_id, feature_ids, columns, scorer)
    streamed = run_ml_signal_incremental(model_id, feature_ids, columns, scorer)
    if len(batch) != len(streamed):
        raise IndicatorError("INDICATOR_ENGINE_MISMATCH")
    if not len(batch):
        return 0.0
    scale = np.maximum(1.0, np.maximum(np.abs(batch), np.abs(streamed)))
    worst = float(np.max(np.abs(batch - streamed) / scale))
    if worst > tolerance:
        raise IndicatorError("INDICATOR_ENGINE_MISMATCH")
    return worst
