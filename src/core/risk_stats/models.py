"""L4_risk_and_safety_v1.0.md#2.2 — VaR/ES 결과 값 객체."""
from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class VarMethod(str, Enum):
    PARAMETRIC = "PARAMETRIC"
    HISTORICAL = "HISTORICAL"
    CORNISH_FISHER = "CORNISH_FISHER"


class VarEs(BaseModel):
    """var_pct/es_pct는 horizon 스케일까지 반영된 손실 비율(0.05=5%), 항상 0 이상."""

    var_pct: Decimal
    es_pct: Decimal
    method: VarMethod
    bars_used: int
    lookback_bars: int
