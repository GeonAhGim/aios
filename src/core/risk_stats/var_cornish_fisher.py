"""L4_risk_and_safety_v1.0.md#2.2 — Cornish-Fisher VaR/ES.

z_cf = z + (z²−1)S/6 + (z³−3z)K/24 − (2z³−5z)S²/36 (S=왜도, K=초과첨도).
closed-form ES가 없어 CF 분위함수를 tail 확률 구간(confidence~1)에서
수치적분(균등 격자 평균)한다.

미검증: 극단적 S/K에서 CF 전개식이 비단조(quantile crossing)일 수 있다는
것은 문헌상 알려진 한계다 — 여기서는 결과적으로 es<var가 나오는 경우만
var_pct로 clamp해 방향을 보정할 뿐, 분위함수 자체의 비단조성을 감지하거나
거부하지는 않는다.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.risk_stats.models import VarEs, VarMethod
from src.core.risk_stats.returns import scale_sigma
from src.core.risk_stats.var_parametric import norm_ppf

_ES_INTEGRATION_STEPS = 500
_EPS = 1e-6


def _cf_quantile(z: float, skew: float, excess_kurt: float) -> float:
    return (
        z
        + (z**2 - 1) * skew / 6
        + (z**3 - 3 * z) * excess_kurt / 24
        - (2 * z**3 - 5 * z) * skew**2 / 36
    )


def cornish_fisher_var_es(
    r: np.ndarray[Any, Any] | Sequence[float],
    *,
    confidence: float,
    horizon_days: float,
    bars_per_day: int,
) -> VarEs:
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 3:
        raise ValueError("cornish_fisher_var_es requires at least 3 return observations")
    sigma = float(np.std(arr, ddof=1))
    deviations = arr - arr.mean()
    m2 = float(np.mean(deviations**2))
    skew = float(np.mean(deviations**3)) / m2**1.5 if m2 > 0 else 0.0
    excess_kurt = float(np.mean(deviations**4)) / m2**2 - 3.0 if m2 > 0 else 0.0

    sigma_h = scale_sigma(sigma, bars_per_day, horizon_days)
    z_cf = _cf_quantile(norm_ppf(confidence), skew, excess_kurt)
    var_pct = max(0.0, z_cf) * sigma_h

    grid = np.linspace(confidence + _EPS, 1 - _EPS, _ES_INTEGRATION_STEPS)
    tail_quantiles = [_cf_quantile(norm_ppf(float(u)), skew, excess_kurt) for u in grid]
    es_raw = float(np.mean(tail_quantiles)) * sigma_h
    es_pct = max(var_pct, es_raw)

    return VarEs(
        var_pct=Decimal(str(var_pct)),
        es_pct=Decimal(str(es_pct)),
        method=VarMethod.CORNISH_FISHER,
        bars_used=int(arr.size),
        lookback_bars=int(arr.size),
    )
