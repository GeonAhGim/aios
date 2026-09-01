"""FD-14(신설) — 가격/지표 알림 API 요청 스키마."""
from __future__ import annotations

from pydantic import BaseModel

from src.services.condition_evaluation import Operator


class AlertCreateRequest(BaseModel):
    exchange: str
    symbol: str
    timeframe: str = "1h"
    indicator: str
    params: dict[str, int] = {}
    operator: Operator
    threshold: float
