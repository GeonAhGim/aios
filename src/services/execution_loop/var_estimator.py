"""FD-8.3 VaR 지표 — Phase 1 Draft 근사치.

정규분포 가정 단순 근사(과거 20틱 수익률 표준편차 기반) — 통계적으로
엄밀한 VaR는 FD-2(시장데이터 히스토리 파이프라인) 확충 후 개선 대상으로
명시한다(기능설계문서_v1.21.md#FD-8.3 처리단계 6, 이 한계를 그대로
문서화한다).
"""
from __future__ import annotations

import math
from decimal import Decimal

import numpy as np

from src.data.models.market_data import Candle

MIN_CANDLES_REQUIRED = 21  # 20개 수익률 계산에 필요한 최소 캔들 수

# 정규분포 단측 z-score — scipy 없이 흔한 신뢰수준만 조회 테이블로 처리.
_Z_SCORES: dict[float, float] = {0.90: 1.282, 0.95: 1.645, 0.975: 1.960, 0.99: 2.326}


def estimate_var_pct(
    candles: list[Candle], *, confidence: float, horizon_days: int
) -> Decimal | None:
    """데이터 부족(캔들 21개 미만) 시 None — RiskEngine이 이를 "판단 불가"로
    거부하지, 조용히 0(무위험)으로 통과시키지 않는다."""
    if len(candles) < MIN_CANDLES_REQUIRED:
        return None

    closes = np.array([float(c.close) for c in candles[-MIN_CANDLES_REQUIRED:]])
    returns = np.diff(closes) / closes[:-1]
    sigma = float(np.std(returns, ddof=1))

    z = _Z_SCORES.get(confidence)
    if z is None:
        z = min(_Z_SCORES.values(), key=lambda known: abs(known - confidence))

    var_pct = z * sigma * math.sqrt(horizon_days) * 100
    return Decimal(str(round(var_pct, 6)))
