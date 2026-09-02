"""L4_risk_and_safety_v1.0.md#2.2 — 경험 분위(선형보간) VaR/ES. h>1은 겹침 합산 수익률.

정규성을 가정하지 않고 관측된 표본 분위수를 직접 쓴다. horizon>1봉이면
겹치는(overlapping) h봉 합산 수익률로 재표본해 다봉 손실을 근사한다.
"""
from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.risk_stats.models import VarEs, VarMethod


def historical_var_es(
    r: np.ndarray[Any, Any] | Sequence[float],
    *,
    confidence: float,
    horizon_days: float,
    bars_per_day: int,
) -> VarEs:
    arr = np.asarray(r, dtype=np.float64)
    h_bars = max(1, round(bars_per_day * horizon_days))
    if arr.size < h_bars:
        raise ValueError(
            f"historical_var_es requires at least {h_bars} observations, got {arr.size}"
        )
    if h_bars == 1:
        horizon_returns = arr
    else:
        cumsum = np.cumsum(np.insert(arr, 0, 0.0))
        horizon_returns = cumsum[h_bars:] - cumsum[:-h_bars]

    alpha = 1 - confidence
    q = float(np.quantile(horizon_returns, alpha, method="linear"))
    var_pct = max(0.0, -q)
    tail = horizon_returns[horizon_returns <= q]
    es_raw = -float(np.mean(tail)) if tail.size else var_pct
    es_pct = max(var_pct, es_raw)  # 분포가 강세 편향이면 es_raw<0이 나올 수 있어 하한 고정

    return VarEs(
        var_pct=Decimal(str(var_pct)),
        es_pct=Decimal(str(es_pct)),
        method=VarMethod.HISTORICAL,
        bars_used=int(horizon_returns.size),
        lookback_bars=int(arr.size),
    )
