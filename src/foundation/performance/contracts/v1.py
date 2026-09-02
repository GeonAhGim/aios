"""Performance Reporting 계약 v1 (FND-09).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.4.

다른 bounded context는 이 파일을 소비하고, domain/models.py를 직접
참조하지 않는다(71번 §4 Contract ownership, 106번 §5).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class StatementScope(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class StatementState(str, Enum):
    ESTIMATED = "ESTIMATED"
    FINAL = "FINAL"
    CORRECTED = "CORRECTED"


class MoneyValue(BaseModel):
    """§3.4 — `amount=None`은 PENDING(아직 리컨실 전이라 값을 낼 수 없음)을
    뜻한다. 0으로 대체하지 않는다(reconciliation의 "never assume zero"와
    같은 원칙)."""

    amount: Decimal | None
    currency: str
    precision: int
    as_of: datetime
    state: Literal["ESTIMATED", "FINAL"]


class ReturnValue(BaseModel):
    value_pct: Decimal | None
    basis: Literal["GROSS", "NET"]
    method: Literal["TWR", "MWR"]
    period_start: datetime
    period_end: datetime
    annualized: bool
    periods_per_year: int | None


class ComponentBreakdown(BaseModel):
    """§3.4 회계 항등식(domain/identity.py)의 입력이자 출력 —
    `gross_pnl - fees - slippage - funding ± fx - estimated_tax = net_pnl`."""

    gross_pnl: MoneyValue
    fees: MoneyValue
    slippage: MoneyValue
    funding: MoneyValue
    fx: MoneyValue
    cashflows_net: MoneyValue
    estimated_tax: MoneyValue
    net_pnl: MoneyValue


class AttributionSliceView(BaseModel):
    """M5 `performance_attribution_slice` 1행 — 통계 하나(예: 전략별 기여도)."""

    dimension: str
    key: str
    contribution: Decimal
    confidence: Decimal | None
    limitation: str | None


class PerformanceMethodologyView(BaseModel):
    """기본 방법론 `pm-v1`(methodology.py) — TWR 기간연결(현금흐름 기초
    반영), MWR=IRR(이분법), 무위험 0, 연환산은 호출부가 `periods_per_year`를
    명시한다."""

    version: str
    methodology_hash: str
    twr_method: str
    mwr_method: str
    risk_free_rate_pct: Decimal
    periods_per_year: int
    schema_version: str = SCHEMA_VERSION


class PerformanceStatementView(BaseModel):
    id: UUID
    tenant_id: UUID
    scope: StatementScope
    scope_ref: str
    period_start: datetime
    period_end: datetime
    as_of: datetime
    methodology_version: str
    methodology_hash: str
    input_refs: list[str]
    """snapshot id / reconciliation run id / fill ids hash 등 — 이 statement가
    어떤 입력으로 계산됐는지(R9 감사 가능성)."""
    components: ComponentBreakdown
    returns: list[ReturnValue]
    risk: dict[str, Decimal | None]
    """vol_pct/mdd_pct/sharpe/calmar — 계산 불가한 값은 None(0 대체 금지)."""
    benchmark: dict[str, Decimal | None] | None
    benchmark_ref: str | None
    state: StatementState
    revision_no: int
    prior_statement_id: UUID | None
    identity_ok: bool
    identity_residual: Decimal | None
    limitations: list[str]
    evidence_refs: list[str]
    schema_version: str = SCHEMA_VERSION


class ComputeStatementCommand(BaseModel):
    scope: StatementScope
    scope_ref: str
    period_start: datetime
    period_end: datetime
    methodology_version: str | None = None
    """None이면 DEFAULT_METHODOLOGY(methodology.py)를 쓴다."""
