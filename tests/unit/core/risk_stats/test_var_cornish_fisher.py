"""L4_risk_and_safety_v1.0.md#R-19 — var_cornish_fisher.py known-value + ES>=VaR."""
import numpy as np
import pytest

from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_cornish_fisher import cornish_fisher_var_es
from src.core.risk_stats.var_parametric import norm_ppf, parametric_var_es

_SKEWED = np.array([-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.06, 0.08, 0.10])


def _hand_cf_quantile(z: float, skew: float, excess_kurt: float) -> float:
    return (
        z
        + (z**2 - 1) * skew / 6
        + (z**3 - 3 * z) * excess_kurt / 24
        - (2 * z**3 - 5 * z) * skew**2 / 36
    )


def test_known_value_matches_hand_computed_cf_quantile():
    sigma = float(np.std(_SKEWED, ddof=1))
    dev = _SKEWED - _SKEWED.mean()
    m2 = float(np.mean(dev**2))
    skew = float(np.mean(dev**3)) / m2**1.5
    kurt = float(np.mean(dev**4)) / m2**2 - 3.0
    z = norm_ppf(0.95)
    expected_var = max(0.0, _hand_cf_quantile(z, skew, kurt)) * sigma

    result = cornish_fisher_var_es(_SKEWED, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(expected_var, rel=1e-6)


def test_known_value_horizon_scaling():
    result_h1 = cornish_fisher_var_es(_SKEWED, confidence=0.95, horizon_days=1, bars_per_day=1)
    result_h4 = cornish_fisher_var_es(_SKEWED, confidence=0.95, horizon_days=4, bars_per_day=1)
    assert float(result_h4.var_pct) == pytest.approx(float(result_h1.var_pct) * 2, rel=1e-9)


def test_symmetric_low_excess_kurtosis_close_to_parametric():
    rng = np.random.default_rng(seed=42)
    sample = rng.normal(loc=0.0, scale=0.01, size=5000)
    cf = cornish_fisher_var_es(sample, confidence=0.95, horizon_days=1, bars_per_day=1)
    parametric = parametric_var_es(sample, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert float(cf.var_pct) == pytest.approx(float(parametric.var_pct), rel=0.05)


def test_es_greater_equal_var_across_confidences():
    for confidence in (0.90, 0.95, 0.99):
        result = cornish_fisher_var_es(
            _SKEWED, confidence=confidence, horizon_days=1, bars_per_day=1
        )
        assert result.es_pct >= result.var_pct


def test_method_recorded():
    result = cornish_fisher_var_es(_SKEWED, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert result.method == VarMethod.CORNISH_FISHER


def test_requires_at_least_three_observations():
    with pytest.raises(ValueError):
        cornish_fisher_var_es(
            np.array([0.01, 0.02]), confidence=0.95, horizon_days=1, bars_per_day=1
        )
