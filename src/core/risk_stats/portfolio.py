"""L4_risk_and_safety_v1.0.md#2.2 — 포트폴리오 수익률·VaR/ES.

파라메트릭은 √(wᵀΣw)(공분산 행렬), 역사적은 가중 합산 수익률 시계열을
그대로 var_historical에 넘긴다.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.risk_stats.models import VarEs, VarMethod
from src.core.risk_stats.var_historical import historical_var_es
from src.core.risk_stats.var_parametric import var_es_from_sigma


def portfolio_returns(
    R: np.ndarray[Any, Any], w: Sequence[Decimal] | np.ndarray[Any, Any]
) -> np.ndarray[Any, Any]:
    """R: (T, N) 봉별 수익률 행렬, w: (N,) 가중치 → (T,) 포트폴리오 수익률."""
    w_arr = np.array([float(x) for x in w], dtype=np.float64)
    result: np.ndarray[Any, Any] = R @ w_arr
    return result


def portfolio_var_es(
    method: VarMethod,
    R: np.ndarray[Any, Any],
    w: Sequence[Decimal] | np.ndarray[Any, Any],
    *,
    confidence: float,
    horizon_days: float,
    bars_per_day: int,
) -> VarEs:
    if method == VarMethod.PARAMETRIC:
        w_arr = np.array([float(x) for x in w], dtype=np.float64)
        cov = np.cov(R, rowvar=False, ddof=1)
        sigma_p = float(np.sqrt(w_arr @ cov @ w_arr))
        return var_es_from_sigma(
            sigma_p,
            confidence=confidence,
            bars_per_day=bars_per_day,
            horizon_days=horizon_days,
            bars_used=R.shape[0],
            lookback_bars=R.shape[0],
            method=VarMethod.PARAMETRIC,
        )
    if method == VarMethod.HISTORICAL:
        pr = portfolio_returns(R, w)
        return historical_var_es(
            pr, confidence=confidence, horizon_days=horizon_days, bars_per_day=bars_per_day
        )
    raise ValueError(f"unsupported portfolio VaR method: {method}")
