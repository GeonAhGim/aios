"""L4_risk_and_safety_v1.0.md#R-20 — portfolio.py 포트폴리오 VaR ≤ Σ(개별 가중 VaR)."""
from decimal import Decimal

import numpy as np
import pytest

from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.portfolio import portfolio_returns, portfolio_var_es
from src.core.risk_stats.var_parametric import parametric_var_es

_R = np.array(
    [
        [0.01, 0.02],
        [-0.02, -0.01],
        [0.03, 0.00],
        [-0.01, 0.02],
        [0.02, -0.03],
        [0.00, 0.01],
        [-0.03, 0.02],
        [0.01, -0.01],
    ]
)
_W = [Decimal("0.5"), Decimal("0.5")]


def test_portfolio_returns_matches_matrix_product():
    result = portfolio_returns(_R, _W)
    expected = _R @ np.array([0.5, 0.5])
    assert result == pytest.approx(expected)


def test_portfolio_var_less_equal_sum_of_weighted_individual_vars():
    portfolio = portfolio_var_es(
        VarMethod.PARAMETRIC, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
    )
    var1 = parametric_var_es(_R[:, 0], confidence=0.95, horizon_days=1, bars_per_day=1)
    var2 = parametric_var_es(_R[:, 1], confidence=0.95, horizon_days=1, bars_per_day=1)
    weighted_sum = Decimal("0.5") * var1.var_pct + Decimal("0.5") * var2.var_pct
    assert portfolio.var_pct <= weighted_sum


def test_portfolio_var_es_known_value_parametric():
    result = portfolio_var_es(
        VarMethod.PARAMETRIC, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
    )
    assert float(result.var_pct) == pytest.approx(0.016990345019945514, rel=1e-6)
    assert result.method == VarMethod.PARAMETRIC
    assert result.bars_used == _R.shape[0]


def test_portfolio_var_es_historical_uses_portfolio_return_series():
    result = portfolio_var_es(
        VarMethod.HISTORICAL, _R, _W, confidence=0.8, horizon_days=1, bars_per_day=1
    )
    assert result.method == VarMethod.HISTORICAL
    assert result.lookback_bars == _R.shape[0]
    assert result.es_pct >= result.var_pct


def test_portfolio_var_es_unsupported_method_raises():
    with pytest.raises(ValueError):
        portfolio_var_es(
            VarMethod.CORNISH_FISHER, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
        )
