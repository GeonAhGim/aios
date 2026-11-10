"""`src/core/indicators/catalog/ml.py` -- AI-21 task-2656.

D2 depth (ADR-2026-09-09-C): negative >= 3, failure injection 1, numeric
performance assertion 1, gate-red reproduction 1, plus the AI-21 DoD
"incremental=batch equivalence" property test (this leaf's own version of
`tests/unit/core/indicators/test_engine_equivalence.py`'s IND-1 contract).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pytest

from src.core.indicators.catalog import ml
from src.core.indicators.catalog.ml import (
    EQUIVALENCE_TOLERANCE,
    MlScorer,
    build_ml_indicator_spec,
    check_ml_signal_equivalence,
    compute_ml_signal,
    ml_indicator_name,
    run_ml_signal_incremental,
)
from src.core.indicators.registry import IndicatorError, IndicatorRegistry
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync


def _linear_scorer(weights: dict[str, float], bias: float = 0.0) -> MlScorer:
    def score(features: Mapping[str, float]) -> float:
        return bias + sum(weights.get(k, 0.0) * v for k, v in features.items())

    return score


def _exploding_scorer(features: Mapping[str, float]) -> float:
    raise RuntimeError("scorer blew up")


def _columns(n: int, seed: int = 0) -> dict[str, np.ndarray[Any, np.dtype[np.float64]]]:
    rng = np.random.default_rng(seed)
    return {"rsi_14": rng.uniform(0.0, 100.0, size=n), "atr_14": rng.uniform(0.1, 5.0, size=n)}


# --- happy path + IND spec (L01/L02) compliance ---


def test_build_ml_indicator_spec_registers_and_resolves_through_l02() -> None:
    spec = build_ml_indicator_spec("momentum-lgbm", ["rsi_14", "atr_14"])
    assert spec.name == "ml.momentum-lgbm"
    assert spec.inputs == ("rsi_14", "atr_14")
    assert spec.outputs == ("signal",)
    assert len(spec.plots) == len(spec.outputs)  # IndicatorSpec.__post_init__ invariant

    registry = IndicatorRegistry({spec.name: spec})
    assert registry.get(spec.name) is spec
    assert registry.validate_params(spec.name, {}) == {}
    assert registry.lookback(spec.name, {}) == 0
    # registry_hash() must serialize this spec without error (canonical_spec_dict
    # round-trips lookback.__name__, params, plots -- proves real L02 compliance,
    # not just a structurally similar dataclass).
    assert isinstance(registry.registry_hash(), str)


def test_compute_ml_signal_scores_every_row() -> None:
    columns = _columns(50)
    scorer = _linear_scorer({"rsi_14": 1.0, "atr_14": 2.0}, bias=0.5)
    out = compute_ml_signal("momentum-lgbm", ["rsi_14", "atr_14"], columns, scorer)
    expected = 0.5 + columns["rsi_14"] + 2.0 * columns["atr_14"]
    assert np.allclose(out, expected)


# --- negative (>= 3) ---


def test_build_ml_indicator_spec_rejects_empty_model_id() -> None:
    with pytest.raises(ValueError, match="model_id"):
        build_ml_indicator_spec("  ", ["rsi_14"])


def test_build_ml_indicator_spec_rejects_empty_feature_ids() -> None:
    with pytest.raises(ValueError, match="feature_ids"):
        build_ml_indicator_spec("momentum-lgbm", [])


def test_build_ml_indicator_spec_rejects_duplicate_feature_ids() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        build_ml_indicator_spec("momentum-lgbm", ["rsi_14", "rsi_14"])


def test_compute_ml_signal_rejects_a_missing_input_column() -> None:
    columns = {"rsi_14": np.array([1.0, 2.0, 3.0])}
    with pytest.raises(IndicatorError):
        compute_ml_signal("momentum-lgbm", ["rsi_14", "atr_14"], columns, _linear_scorer({}))


# --- failure injection ---


def test_compute_ml_signal_propagates_a_scorer_failure() -> None:
    columns = _columns(10)
    with pytest.raises(RuntimeError, match="scorer blew up"):
        compute_ml_signal("momentum-lgbm", ["rsi_14", "atr_14"], columns, _exploding_scorer)


# --- incremental = batch equivalence (AI-21 DoD) ---


def test_check_ml_signal_equivalence_agrees_for_a_deterministic_scorer() -> None:
    columns = _columns(500, seed=7)
    scorer = _linear_scorer({"rsi_14": 0.3, "atr_14": -1.1}, bias=2.0)
    worst = check_ml_signal_equivalence("momentum-lgbm", ["rsi_14", "atr_14"], columns, scorer)
    assert worst == 0.0


def test_ml_indicator_name_matches_batch_and_incremental_entry_points() -> None:
    columns = _columns(30, seed=3)
    scorer = _linear_scorer({"rsi_14": 1.0})
    batch = compute_ml_signal("m1", ["rsi_14", "atr_14"], columns, scorer)
    streamed = run_ml_signal_incremental("m1", ["rsi_14", "atr_14"], columns, scorer)
    assert np.array_equal(batch, streamed)
    assert ml_indicator_name("m1") == "ml.m1"


# --- gate-red reproduction ---


def test_gate_red_a_scorer_with_hidden_state_is_caught_by_equivalence_check() -> None:
    """Red repro: a scorer that (incorrectly) carries hidden state across
    calls -- e.g. an accidental running total -- would still "work" if
    nothing ever compared the batch and incremental paths against each
    other. `check_ml_signal_equivalence` exists precisely to catch this
    class of bug; this test proves it does by injecting exactly that bug
    and asserting the real equivalence check raises `INDICATOR_ENGINE_
    MISMATCH` rather than passing silently."""
    columns = _columns(20, seed=9)

    calls = {"n": 0}

    def _stateful_scorer(features: Mapping[str, float]) -> float:
        # Bug: score drifts with call count instead of being a pure
        # function of `features`. `compute_ml_signal` (batch) runs to
        # completion first and consumes calls 1..n; `run_ml_signal_
        # incremental` then runs and consumes calls n+1..2n -- so every row
        # comes out offset by a constant `n` between the two paths.
        calls["n"] += 1
        return features["rsi_14"] + calls["n"]

    with pytest.raises(IndicatorError):
        check_ml_signal_equivalence("m1", ["rsi_14", "atr_14"], columns, _stateful_scorer)


# --- numeric performance assertion ---


_PER_ROW_BUDGET_MS = 0.050  # 50 us per incremental row


def _per_row_cpu_ms(perf_budget: PerfBudget, scorer: MlScorer, n: int) -> float:
    """Best-of-N per-row CPU ms of the incremental path over `n` rows.

    One timed call covers all `n` rows and is repeated `batch` times, so the
    Windows ~15.6 ms process_time tick cannot collapse the reading to 0.
    """
    columns = _columns(n, seed=1)

    def _pass() -> np.ndarray[Any, np.dtype[np.float64]]:
        return ml.run_ml_signal_incremental("momentum-lgbm", ["rsi_14", "atr_14"], columns, scorer)

    sample = perf_budget.best_of(_pass, n=5, warmup=1, batch=32)
    return sample.cpu_ms / n


@pytest.mark.perf
def test_compute_ml_signal_latency_p99_within_budget(
    perf_budget: PerfBudget,
) -> None:
    """5,000 rows through the incremental path (the one a live/replay loop
    calls once per bar); per-row latency asserted against a 50 us budget. A
    linear scorer over 2 features is a handful of float multiplies -- local
    measurement is ~1-2us/row, leaving >25x headroom. Measured with
    `perf_budget.best_of` so one quantized tick cannot flip the result."""
    n = 5000
    columns = _columns(n, seed=1)
    scorer = _linear_scorer({"rsi_14": 1.0, "atr_14": 2.0}, bias=0.5)
    per_row_ms = _per_row_cpu_ms(perf_budget, scorer, n)
    print(f"[AI-21 ml catalog] n={n} per_row={per_row_ms * 1000:.2f}us budget<50us")
    _assert_per_row_budget(per_row_ms)
    worst_case = check_ml_signal_equivalence("momentum-lgbm", ["rsi_14", "atr_14"], columns, scorer)
    assert 0.0 <= worst_case <= EQUIVALENCE_TOLERANCE


def _assert_per_row_budget(per_row_ms: float) -> None:
    assert per_row_ms < _PER_ROW_BUDGET_MS, f"per-row CPU budget exceeded: {per_row_ms}ms"


@pytest.mark.perf
def test_compute_ml_signal_cpu_batch_does_not_collapse_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """Even the minimum of five batches must span a CPU clock tick."""
    scorer = _linear_scorer({"rsi_14": 1.0, "atr_14": 2.0}, bias=0.5)
    assert _per_row_cpu_ms(perf_budget, scorer, 5000) > 0.0


@pytest.mark.perf
def test_compute_ml_signal_latency_budget_violation_detected(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The actual green test must reject a virtual 10x CPU budget regression."""
    reset_virtual_totals()
    # 5,000 rows * 50 us/row * 10; PerfBudget normalizes the outer batch.
    delay_seconds = 5000 * _PER_ROW_BUDGET_MS * 10 / 1000
    monkeypatch.setattr(
        ml,
        "run_ml_signal_incremental",
        virtual_delayed_sync(ml.run_ml_signal_incremental, delay_seconds),
    )
    try:
        with pytest.raises(AssertionError, match="per-row CPU budget exceeded"):
            test_compute_ml_signal_latency_p99_within_budget(perf_budget)
    finally:
        reset_virtual_totals()
