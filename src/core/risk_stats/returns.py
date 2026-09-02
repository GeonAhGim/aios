"""L4_risk_and_safety_v1.0.md#2.2 — 캔들→log 수익률, timeframe→bars_per_day, horizon 스케일.

R4 버그 근거: 기존 구현은 1분봉 표준편차에 √days만 곱해 봉 단위 환산이
빠진 채(단위 불일치) horizon을 스케일했다. scale_sigma가 bars_per_day를
필수 인자로 받게 해 호출부가 이 환산을 건너뛸 수 없게 한다.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

import numpy as np

_BARS_PER_DAY: dict[str, int] = {
    "1m": 1440,
    "3m": 480,
    "5m": 288,
    "15m": 96,
    "30m": 48,
    "1h": 24,
    "2h": 12,
    "4h": 6,
    "6h": 4,
    "12h": 2,
    "1d": 1,
}


def log_returns(closes: Sequence[Decimal]) -> np.ndarray[Any, Any]:
    """길이 n 종가 → 길이 n-1 log 수익률(np.diff(log(price))). n<2면 빈 배열."""
    if len(closes) < 2:
        return np.array([], dtype=np.float64)
    prices = np.array([float(c) for c in closes], dtype=np.float64)
    return np.diff(np.log(prices))


def bars_per_day(timeframe: str) -> int:
    """미지원 timeframe은 ValueError — 무음으로 잘못된 스케일을 적용하지 않는다."""
    try:
        return _BARS_PER_DAY[timeframe]
    except KeyError as exc:
        raise ValueError(f"unsupported timeframe: {timeframe!r}") from exc


def scale_sigma(sigma: float, bars_per_day: int, horizon_days: float) -> float:
    """sqrt-time 스케일: sigma_horizon = sigma_per_bar * sqrt(bars_per_day * horizon_days)."""
    return sigma * math.sqrt(bars_per_day * horizon_days)
