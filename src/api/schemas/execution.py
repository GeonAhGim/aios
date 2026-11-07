"""16번 — 실행 제어판 API 요청·응답 스키마."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from src.services.execution_monitoring_service import ExecutionCard, LastRiskVerdict
from src.services.execution_service import ExecutionSummary


class ExecutionCreateRequest(BaseModel):
    strategy_id: str
    strategy_version: str
    allocated_capital: Decimal
    currency: str
    exchange: str
    mode: str


class ConvertToLiveRequest(BaseModel):
    """모의 -> 실전 전환 요청 (J8 LP-3).
    사용자가 명시한 손실 한도·초기 노출과 재인증이 필수다."""

    allocated_capital: Decimal
    currency: str
    exchange: str
    max_loss_amount: Decimal = Field(gt=0)
    initial_exposure_pct: Decimal = Field(ge=1, le=100)
    password: str = Field(min_length=1)
    totp_code: str | None = None

    @model_validator(mode="after")
    def _loss_limit_within_allocation(self) -> ConvertToLiveRequest:
        if self.max_loss_amount > self.allocated_capital:
            raise ValueError("max_loss_amount는 배정 자금(allocated_capital) 이하여야 합니다.")
        return self


class ExpandExposureRequest(BaseModel):
    """LIVE 실행의 허용 자본 확대 (J8 LP-4). 재인증 필수, 확대만 가능하다."""

    exposure_pct: Decimal = Field(gt=0, le=100)
    currency: str
    exchange: str
    password: str = Field(min_length=1)
    totp_code: str | None = None


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
    last_risk_verdict: LastRiskVerdict | None


def to_execution_card_response(card: ExecutionCard) -> ExecutionCardResponse:
    return ExecutionCardResponse(**card.model_dump())
