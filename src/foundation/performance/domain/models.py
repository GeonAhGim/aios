"""Performance Reporting 도메인 모델 — pure value object.

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§3.4.

표시 메타데이터(통화/정밀도/as_of별 상태)는 여기 없다 — 그건
`contracts/v1.MoneyValue`의 몫이다(71번 §4 경계, "domain은 순수 계산,
contracts는 소비자 대상 표현"). 여기 `Decimal | None`에서 None은 항상
PENDING(계산 불가/미리컨실)을 뜻한다 — 0으로 대체하지 않는다
(reconciliation의 "never assume zero"와 같은 원칙, identity.py 참조).
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
    """M5 `valuation_snapshot` — statement 계산의 원재료 하나. `state`가
    RECONCILED가 아니면(즉 ESTIMATED) statement는 그 기간 전체를 PENDING
    처리해야 한다(paper_input_adapter.py, L48)."""

    id: UUID
    tenant_id: UUID
    scope: str
    """"PAPER" | "LIVE" — contracts.StatementScope와 값이 1:1이지만, domain은
    enum 자체의 "의미"를 소유하지 않는다(그건 contract 몫, 71번 §4)."""
    scope_ref: str
    as_of: datetime
    positions: tuple[dict[str, object], ...]
    cash: Decimal
    price_evidence: tuple[str, ...]
    reconciliation_run_id: UUID | None
    state: ValuationState


@dataclass(frozen=True)
class ComponentBreakdown:
    """§3.4 회계 항등식(domain/identity.py)의 입력·출력 —
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
    """M5 `performance_attribution_slice` 1행 — 항상 statement 하나에
    속한다(reconciliation의 `ReconciliationItem.run_id`와 같은 이유로
    `statement_id`를 갖는다)."""

    statement_id: UUID
    dimension: str
    key: str
    contribution: Decimal
    confidence: Decimal | None
    limitation: str | None


@dataclass(frozen=True)
class Methodology:
    """M5 `performance_methodology` — 버전화된 계산 방법론(R2). 필드 값이
    하나라도 바뀌면 새 버전을 만든다(같은 버전 재정의 금지, WORM과 같은
    이유)."""

    version: str
    methodology_hash: str
    twr_method: str
    mwr_method: str
    risk_free_rate_pct: Decimal
    periods_per_year: int


@dataclass(frozen=True)
class PerformanceStatement:
    """M5 `performance_statement` — append-only(WORM, `REVOKE UPDATE/DELETE`).
    정정은 `state=CORRECTED`인 새 리비전을 만든다(`prior_statement_id`로
    체인, correct_statement.py 참조) — 원본 행은 절대 고치지 않는다."""

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
