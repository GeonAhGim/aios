"""19번 — 포트폴리오 API 요청 스키마."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from src.services.portfolio_service import RebalanceAdjustment


class RebalanceAdjustmentRequest(BaseModel):
    execution_id: int
    new_allocated_capital: Decimal


class RebalanceRequest(BaseModel):
    adjustments: list[RebalanceAdjustmentRequest]


def to_adjustments(body: RebalanceRequest) -> list[RebalanceAdjustment]:
    return [
        RebalanceAdjustment(
            execution_id=a.execution_id, new_allocated_capital=a.new_allocated_capital
        )
        for a in body.adjustments
    ]
