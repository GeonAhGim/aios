"""Performance Reporting domain models — pure value object.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.4.

Display metadata (currency/precision/per-as_of status) does not live here —
that belongs to `contracts/v1.MoneyValue` (task-71 §4 boundary: "domain does
pure computation, contracts owns consumer-facing presentation"). Here, `None`
in `Decimal | None` always means PENDING (not yet computable / not yet
reconciled) — it is never substituted with 0 (same "never assume zero"
principle as reconciliation, see identity.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID


class ValuationState(str, Enum):
    ESTIMATED = "ESTIMATED"
    RECONCILED = "RECONCILED"


class StatementState(str, Enum):
    ESTIMATED = "ESTIMATED"
    FINAL = "FINAL"
    CORRECTED = "CORRECTED"


class CashflowKind(str, Enum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"


@dataclass(frozen=True)
class Cashflow:
    at: datetime
    amount: Decimal
    kind: CashflowKind


@dataclass(frozen=True)
class ValuationSnapshot:
    """M5 `valuation_snapshot` — one raw input to a statement computation. If
    `state` is not RECONCILED (i.e. ESTIMATED), the statement must treat the
    entire period as PENDING (paper_input_adapter.py, L48)."""

    id: UUID
    tenant_id: UUID
    scope: str
    """"PAPER" | "LIVE" — matches `contracts.StatementScope` value-for-value,
    but domain does not own the "meaning" of the enum itself (that belongs to
    the contract, task-71 §4)."""
    scope_ref: str
    as_of: datetime
    positions: tuple[dict[str, object], ...]
    cash: Decimal
    price_evidence: tuple[str, ...]
    reconciliation_run_id: UUID | None
    state: ValuationState


@dataclass(frozen=True)
class ComponentBreakdown:
    """Inputs/outputs of the §3.4 accounting identity (domain/identity.py) —
    `gross_pnl - fees - slippage - funding ± fx - estimated_tax = net_pnl`."""

    gross_pnl: Decimal | None
    fees: Decimal | None
    slippage: Decimal | None
    funding: Decimal | None
    fx: Decimal | None
    cashflows_net: Decimal | None
    estimated_tax: Decimal | None
    net_pnl: Decimal | None


@dataclass(frozen=True)
class ReturnFigure:
    value_pct: Decimal | None
    basis: str
    """"GROSS" | "NET" """
    method: str
    """"TWR" | "MWR" """
    period_start: datetime
    period_end: datetime
    annualized: bool
    periods_per_year: int | None


@dataclass(frozen=True)
class AttributionSlice:
    """One row of M5 `performance_attribution_slice` — always belongs to
    exactly one statement (carries `statement_id` for the same reason
    reconciliation's `ReconciliationItem` carries `run_id`)."""

    statement_id: UUID
    dimension: str
    key: str
    contribution: Decimal
    confidence: Decimal | None
    limitation: str | None


@dataclass(frozen=True)
class Methodology:
    """M5 `performance_methodology` — versioned computation methodology (R2).
    If any field value changes, a new version is created (redefining an
    existing version is forbidden, same reasoning as WORM)."""

    version: str
    methodology_hash: str
    twr_method: str
    mwr_method: str
    risk_free_rate_pct: Decimal
    periods_per_year: int


@dataclass(frozen=True)
class PerformanceStatement:
    """M5 `performance_statement` — append-only (WORM, `REVOKE UPDATE/DELETE`).
    A correction creates a new revision with `state=CORRECTED` (chained via
    `prior_statement_id`, see correct_statement.py) — the original row is
    never modified."""

    id: UUID
    tenant_id: UUID
    scope: str
    scope_ref: str
    period_start: datetime
    period_end: datetime
    as_of: datetime
    methodology_version: str
    methodology_hash: str
    input_refs: tuple[str, ...]
    components: ComponentBreakdown
    returns: tuple[ReturnFigure, ...]
    risk: dict[str, Decimal | None]
    benchmark: dict[str, Decimal | None] | None
    benchmark_ref: str | None
    state: StatementState
    revision_no: int
    prior_statement_id: UUID | None
    identity_ok: bool
    identity_residual: Decimal | None
    limitations: tuple[str, ...] = field(default_factory=tuple)
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)
