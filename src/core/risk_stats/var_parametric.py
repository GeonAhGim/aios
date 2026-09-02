"""L4_risk_and_safety_v1.0.md#2.2 — 정규분포 가정 VaR/ES: VaR=zσ√h, ES=σφ(z)/(1−c)√h.

z=Φ⁻¹(c)는 scipy 없이(§2.2 "순수·numpy만") Acklam(2003) 유리함수 근사로 계산한다
— 상대오차 ≤1.15e-9로, 금융권이 실제 쓰는 신뢰수준(0.90~0.999) 범위에서
Decimal 변환 시 버려지는 정밀도보다 작다(미검증: 특정 거래소 벤더 구현과의
bit-for-bit 일치는 확인하지 않았다). ES≥VaR는 정규분포에서 항상 성립하는
항등식이라 별도 clamp를 두지 않는다 — Cornish-Fisher처럼 근사식이 비단조가
될 수 있는 경우에만 clamp가 필요하다.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.risk_stats.models import VarEs, VarMethod
from src.core.risk_stats.returns import scale_sigma

_ACKLAM_A = (
    -3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
    1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00,
)
_ACKLAM_B = (
    -5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
    6.680131188771972e01, -1.328068155288572e01,
)
_ACKLAM_C = (
    -7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
    -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00,
)
_ACKLAM_D = (
    7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
    3.754408661907416e00,
)
_P_LOW = 0.02425


def norm_ppf(p: float) -> float:
    """표준정규 분위함수 Φ⁻¹(p). p∈(0,1) 필수(fail-closed)."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p must be in (0, 1), got {p}")
    a, b, c, d = _ACKLAM_A, _ACKLAM_B, _ACKLAM_C, _ACKLAM_D
    if p < _P_LOW:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 1 - _P_LOW:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1
    )


def norm_pdf(x: float) -> float:
    """표준정규 확률밀도함수 φ(x)."""
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def var_es_from_sigma(
    sigma: float,
    *,
    confidence: float,
    bars_per_day: int,
    horizon_days: float,
    bars_used: int,
    lookback_bars: int,
    method: VarMethod = VarMethod.PARAMETRIC,
) -> VarEs:
    """이미 계산된 봉 단위 sigma로부터 정규분포 VaR/ES — portfolio.py와 공유."""
    z = norm_ppf(confidence)
    sigma_h = scale_sigma(sigma, bars_per_day, horizon_days)
    var = z * sigma_h
    es = norm_pdf(z) / (1 - confidence) * sigma_h
    return VarEs(
        var_pct=Decimal(str(var)),
        es_pct=Decimal(str(es)),
        method=method,
        bars_used=bars_used,
        lookback_bars=lookback_bars,
    )


def parametric_var_es(
    r: np.ndarray[Any, Any] | Sequence[float],
    *,
    confidence: float,
    horizon_days: float,
    bars_per_day: int,
) -> VarEs:
    arr = np.asarray(r, dtype=np.float64)
    if arr.size < 2:
        raise ValueError("parametric_var_es requires at least 2 return observations")
    sigma = float(np.std(arr, ddof=1))
    return var_es_from_sigma(
        sigma,
        confidence=confidence,
        bars_per_day=bars_per_day,
        horizon_days=horizon_days,
        bars_used=arr.size,
        lookback_bars=arr.size,
        method=VarMethod.PARAMETRIC,
    )
