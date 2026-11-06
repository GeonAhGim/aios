"""L4_risk_and_safety_v1.0.md#R-19 — cornish_fisher perf budget (ADR-2026-09-09-C D1)."""

from time import perf_counter

import numpy as np
import pytest

from src.core.risk_stats.var_cornish_fisher import cornish_fisher_var_es

_SKEWED = np.array([-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.06, 0.08, 0.10])


@pytest.mark.perf
def test_default_250_bar_calculation_p95_under_5ms():
    """ADR-2026-09-09-C D1: component ceiling within the 5ms risk-gate budget.

    This component p95 check does not claim end-to-end gate p99 compliance.
    Default daily lookback=250 (L4 risk R4); setup/warmup are not timed.
    Assertions moved outside the timed loop to avoid measuring assert overhead.
    """
    returns = np.random.default_rng(8333).normal(0, 0.01, 250)
    kwargs = {"confidence": 0.95, "horizon_days": 1, "bars_per_day": 1}
    for _ in range(5):
        cornish_fisher_var_es(returns, **kwargs)
    durations = []
    for _ in range(100):
        started = perf_counter()
        result = cornish_fisher_var_es(returns, **kwargs)
        durations.append(perf_counter() - started)
    # Sanity assertions outside timed loop
    assert result.es_pct >= result.var_pct >= 0
    assert result.bars_used == 250
    p95 = float(np.quantile(durations, 0.95))
    assert p95 < 0.005, f"250-bar calculation p95={p95 * 1000:.3f}ms >= 5ms"


@pytest.mark.perf
def test_es_gte_var_invariant_under_load():
    """Verify ES ≥ VaR invariant holds across many calls (no timing assertion)."""
    returns = np.random.default_rng(9999).normal(0, 0.01, 250)
    kwargs = {"confidence": 0.95, "horizon_days": 1, "bars_per_day": 1}
    for _ in range(200):
        result = cornish_fisher_var_es(returns, **kwargs)
        assert result.es_pct >= result.var_pct


@pytest.mark.perf
def test_horizon_scaling_p95_under_5ms():
    """Horizon scaling (sqrt(t)) within the 5ms budget."""
    returns = np.random.default_rng(8333).normal(0, 0.01, 250)
    kwargs = {"confidence": 0.95, "horizon_days": 4, "bars_per_day": 1}
    for _ in range(5):
        cornish_fisher_var_es(returns, **kwargs)
    durations = []
    for _ in range(100):
        started = perf_counter()
        cornish_fisher_var_es(returns, **kwargs)
        durations.append(perf_counter() - started)
    p95 = float(np.quantile(durations, 0.95))
    assert p95 < 0.005, f"horizon=4 calculation p95={p95 * 1000:.3f}ms >= 5ms"
