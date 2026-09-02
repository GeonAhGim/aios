"""L4_risk_and_safety_v1.0.md#2.2 — 정렬된 수익률의 Pearson 상관(옵션 EWMA), 최소 겹침 미달=None."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np


def _weighted_pearson(
    x: np.ndarray[Any, Any], y: np.ndarray[Any, Any], ewma_lambda: float | None
) -> float | None:
    n = x.size
    weights = np.ones(n) if ewma_lambda is None else ewma_lambda ** np.arange(n - 1, -1, -1)
    weights = weights / weights.sum()
    mx = float(np.sum(weights * x))
    my = float(np.sum(weights * y))
    vx = float(np.sum(weights * (x - mx) ** 2))
    vy = float(np.sum(weights * (y - my) ** 2))
    if vx <= 0 or vy <= 0:
        return None  # 상수 시계열 — 상관 미정의
    cov = float(np.sum(weights * (x - mx) * (y - my)))
    return cov / math.sqrt(vx * vy)


def pearson_matrix(
    R: Mapping[str, Sequence[float] | np.ndarray[Any, Any]],
    *,
    min_overlap: int,
    ewma_lambda: float | None = None,
) -> dict[tuple[str, str], float | None]:
    arrays = {sym: np.asarray(v, dtype=np.float64) for sym, v in R.items()}
    symbols = list(arrays)
    result: dict[tuple[str, str], float | None] = {}
    for i, a in enumerate(symbols):
        for b in symbols[i:]:
            xa, xb = arrays[a], arrays[b]
            n = min(xa.size, xb.size)
            mask = ~np.isnan(xa[:n]) & ~np.isnan(xb[:n])
            overlap = int(mask.sum())
            corr = (
                _weighted_pearson(xa[:n][mask], xb[:n][mask], ewma_lambda)
                if overlap >= min_overlap
                else None
            )
            result[(a, b)] = corr
            result[(b, a)] = corr
    return result
