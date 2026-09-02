"""L4_risk_and_safety_v1.0.md#R-19 — var_parametric.py known-value + ES>=VaR."""
from decimal import Decimal

import numpy as np
import pytest

from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_parametric import norm_ppf, parametric_var_es

_R = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])


def test_known_value_95_confidence_horizon_1():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.02600741939377787, rel=1e-6)
    assert float(result.es_pct) == pytest.approx(0.03261435315261965, rel=1e-6)


def test_known_value_99_confidence_horizon_1():
    result = parametric_var_es(_R, confidence=0.99, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.036782789559297764, rel=1e-6)


def test_known_value_95_confidence_horizon_4_days():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=4, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.05201483878755574, rel=1e-6)


def test_es_greater_equal_var_across_confidences():
    for confidence in (0.90, 0.95, 0.975, 0.99, 0.999):
        result = parametric_var_es(_R, confidence=confidence, horizon_days=1, bars_per_day=1)
        assert result.es_pct >= result.var_pct


def test_method_and_bar_counts_recorded():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert result.method == VarMethod.PARAMETRIC
    assert result.bars_used == len(_R)
    assert result.lookback_bars == len(_R)


def test_parametric_var_es_requires_at_least_two_observations():
    with pytest.raises(ValueError):
        parametric_var_es(np.array([0.01]), confidence=0.95, horizon_days=1, bars_per_day=1)


def test_norm_ppf_rejects_out_of_range_probability():
    with pytest.raises(ValueError):
        norm_ppf(0.0)
    with pytest.raises(ValueError):
        norm_ppf(1.0)


def test_var_pct_is_decimal():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert isinstance(result.var_pct, Decimal)
