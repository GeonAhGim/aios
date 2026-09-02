"""16번 — 실행 제어판 API 요청·응답 스키마."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from src.services.execution_monitoring_service import ExecutionCard
from src.services.execution_service import ExecutionSummary


class ExecutionCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    allocated_capital: Decimal
    currency: str
    exchange: str
    mode: str


class ConvertToLiveRequest(BaseModel):
    allocated_capital: Decimal
    currency: str
    exchange: str


class RetireRequest(BaseModel):
    liquidation: str = "KEEP_POSITIONS"


class SetMaxDrawdownRequest(BaseModel):
    max_drawdown_pct: Decimal | None = None


class ExecutionResponse(BaseModel):
    id: int
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    approval_request_id: int | None = None
    max_drawdown_pct: Decimal | None = None


def to_execution_response(summary: ExecutionSummary) -> ExecutionResponse:
    return ExecutionResponse(
        id=summary.id,
        status=summary.status,
        mode=summary.mode,
        exchange=summary.exchange,
        allocated_capital=summary.allocated_capital,
        approval_request_id=summary.approval_request_id,
        max_drawdown_pct=summary.max_drawdown_pct,
    )


class ExecutionCardResponse(BaseModel):
    execution_id: int
    strategy_id: str
    strategy_version: str
    status: str
    mode: str
    exchange: str
    allocated_capital: Decimal
    days_since_start: int | None
    realized_pnl: Decimal
    unrealized_pnl: Decimal
    max_drawdown_pct: Decimal | None


def to_execution_card_response(card: ExecutionCard) -> ExecutionCardResponse:
    return ExecutionCardResponse(**card.model_dump())
