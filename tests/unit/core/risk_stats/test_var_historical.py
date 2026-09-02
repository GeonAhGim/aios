"""L4_risk_and_safety_v1.0.md#R-19 — var_historical.py known-value + ES>=VaR + h>1 겹침합산."""
import numpy as np
import pytest

from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_historical import historical_var_es

_R = np.array([-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06])


def test_known_value_90_confidence_horizon_1():
    result = historical_var_es(_R, confidence=0.9, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.032, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.05, rel=1e-9)


def test_known_value_80_confidence_horizon_1():
    result = historical_var_es(_R, confidence=0.8, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.014, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.04, rel=1e-9)


def test_known_value_horizon_2_bars_uses_overlapping_sums():
    # bars_per_day=1, horizon_days=2 -> h_bars=2, 겹침 합산 수익률 9개(=10-2+1)
    result = historical_var_es(_R, confidence=0.9, horizon_days=2, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.048, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.08, rel=1e-9)
    assert result.bars_used == 9
    assert result.lookback_bars == 10


def test_es_greater_equal_var_across_confidences():
    for confidence in (0.5, 0.8, 0.9, 0.95):
        result = historical_var_es(_R, confidence=confidence, horizon_days=1, bars_per_day=1)
        assert result.es_pct >= result.var_pct


def test_method_recorded():
    result = historical_var_es(_R, confidence=0.9, horizon_days=1, bars_per_day=1)
    assert result.method == VarMethod.HISTORICAL


def test_insufficient_observations_for_horizon_raises():
    with pytest.raises(ValueError):
        historical_var_es(np.array([0.01, 0.02]), confidence=0.9, horizon_days=5, bars_per_day=1)
