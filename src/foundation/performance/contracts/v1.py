"""Performance Reporting Contract v1 (FND-09).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.4.

Other bounded contexts consume this file and must not reference domain/models.py
directly (§4 Contract ownership rule 71, §5 rule 106).
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
    """§3.4 — `amount=None` means PENDING (reconciliation not yet complete,
    value unavailable). Do not substitute zero (principle: "never assume zero"
    from reconciliation)."""

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
    """§3.4 Input and output of the accounting identity (domain/identity.py):
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
    """One row of M5 `performance_attribution_slice` — a single statistic
    (e.g., per-strategy contribution)."""

    dimension: str
    key: str
    contribution: Decimal
    confidence: Decimal | None
    limitation: str | None


class PerformanceMethodologyView(BaseModel):
    """Default methodology `pm-v1` (methodology.py) — TWR time-weighted
    (cash-flow based), MWR=IRR (dichotomous), risk-free rate 0, annualization
    requires caller to specify `periods_per_year`."""

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
    """Snapshot id / reconciliation run id / fill ids hash etc. — which inputs
    this statement was computed from (R9 auditability)."""
    components: ComponentBreakdown
    returns: list[ReturnValue]
    risk: dict[str, Decimal | None]
    """vol_pct/mdd_pct/sharpe/calmar — None where uncomputable (do not substitute 0)."""
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
    """If None, uses DEFAULT_METHODOLOGY (methodology.py)."""
