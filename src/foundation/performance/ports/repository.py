"""Performance Reporting repository/input port. The domain knows only this
Protocol; actual implementations (adapters/) remain unknown (71 §4).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from src.foundation.performance.domain.models import (
    AttributionSlice,
    Cashflow,
    Methodology,
    PerformanceStatement,
    ValuationSnapshot,
)


class PerformanceRepository(Protocol):
    async def get_methodology(self, version: str) -> Methodology | None: ...

    async def insert_methodology(self, methodology: Methodology) -> Methodology:
        """The version string acts as a content address (methodology_hash fully
        defines the body), so if it already exists we skip re-insertion and
        return the existing row (implementation guarantees idempotency via
        `ON CONFLICT DO NOTHING` + re-query)."""
        ...

    async def insert_statement(self, statement: PerformanceStatement) -> PerformanceStatement:
        """M5 `performance_statement` is `REVOKE UPDATE, DELETE` (WORM) — only
        appends are allowed. Corrections are expressed by inserting another
        revision (state=CORRECTED) (correct_statement.py, L49)."""
        ...

    async def get_statement(
        self, statement_id: UUID
    ) -> PerformanceStatement | None: ...

    async def list_statements(
        self, *, tenant_id: UUID, scope: str | None = None
    ) -> tuple[PerformanceStatement, ...]: ...

    async def get_latest_statement(
        self, *, tenant_id: UUID, scope: str, scope_ref: str, period_start: datetime,
        period_end: datetime, methodology_version: str,
    ) -> PerformanceStatement | None:
        """The latest revision for the same (tenant, scope, scope_ref, period,
        methodology_version) — used to check whether a correction exists or to
        chain `prior_statement_id`. Reason `methodology_version` is part of the
        key (PRF-009): when the methodology changes, the revision number must
        start a new lineage rather than continuing the previous one, so that
        the revision number itself signals "new statement" rather than
        "quiet recalculation"."""
        ...

    async def insert_attribution(self, slice_: AttributionSlice) -> AttributionSlice:
        """`slice_.statement_id` points to the target statement. We do not
        bundle parent-child into a single call (unlike reconciliation's
        `insert_run_with_items`) because attribution is a separate optional
        decomposition step that runs after the statement is computed — L49
        defines the actual call order."""
        ...

    async def list_attribution(self, statement_id: UUID) -> tuple[AttributionSlice, ...]: ...


class StatementInputPort(Protocol):
    """Input assembly per scope (PAPER/LIVE) — `PaperStatementInputAdapter`
    (L48) implements this port. compute_statement.py (L49) knows nothing
    about scopes; it gets all inputs needed for computation through this
    single port."""

    async def load_reconciled_snapshots(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[ValuationSnapshot, ...]:
        """If periods that are not RESOLVED (pre-reconciliation) are mixed in,
        express that fact itself as
        `ValuationSnapshot.state != RECONCILED` — do not silently filter
        them out (let the caller decide)."""
        ...

    async def load_fills(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[dict[str, object], ...]:
        """Fill ledger (includes fees and execution prices) — the structure is
        owned by the adapter (71 §4; performance does not need to know
        paper_control's raw schema directly)."""
        ...

    async def load_cashflows(
        self, *, scope_ref: str, period_start: datetime, period_end: datetime
    ) -> tuple[Cashflow, ...]: ...
