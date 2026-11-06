"""전환 근거 응답 스키마 (J8 LP-2). 값이 없는 항목은 null이고 사유가 `unavailable`에 있다."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.services.live_readiness import LiveReadiness


class LiveReadinessResponse(BaseModel):
    order_count: int
    terminal_count: int
    open_count: int
    unknown_count: int
    filled_count: int
    partial_fill_count: int
    unfilled_count: int
    rejected_count: int
    fill_ratio: Decimal | None
    partial_fill_ratio: Decimal | None
    unfilled_ratio: Decimal | None
    reject_ratio: Decimal | None
    period_start: datetime | None
    period_end: datetime | None
    period_days: Decimal | None
    avg_slippage_bps: Decimal | None
    slippage_sample_size: int
    fee_totals: dict[str, Decimal] | None
    net_pnl: Decimal | None
    recovery_event_count: int | None
    unavailable: dict[str, str] = Field(default_factory=dict)


def to_live_readiness_response(value: LiveReadiness) -> LiveReadinessResponse:
    return LiveReadinessResponse(**value.__dict__)
