"""ComputeStatement command — §2 pipeline 81.

select reconciled snapshots/fills/cashflows → apply methodology version →
value positions → compute costs/returns/risk → validate identity → persist
immutable statement + evidence.

Limitations (explicit, inherited from paper_input_adapter.py scope reduction):
`StatementInputPort` always provides exactly one snapshot (current point), so
it cannot supply the two boundary values needed for TWR/MWR computation —
`returns.value_pct` is always `None` (PENDING). `fees`/`slippage`/`funding`/`fx`/`estimated_tax`
are also always `None` because the ledger lacks those columns. Only
`gross_pnl` (sum of position realized+unrealized PnL) and
`cashflows_net` (sum of allocated_capital at execution start) are actually
populated. We do not substitute zeros (PRF-002) — what this leaf truly
validates is the discipline itself: "do not force the identity to pass when
input is insufficient."
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from src.core.observability.metric_names import (
    PERFORMANCE_STATEMENT_COUNT_TOTAL,
    PERFORMANCE_STATEMENT_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.application.statement_projection import statement_to_view
from src.foundation.performance.contracts.v1 import (
    ComputeStatementCommand,
    PerformanceStatementView,
)
from src.foundation.performance.domain.methodology import DEFAULT_METHODOLOGY
from src.foundation.performance.domain.models import (
    Cashflow,
    CashflowKind,
    ComponentBreakdown,
    PerformanceStatement,
    ReturnFigure,
    StatementState,
    ValuationSnapshot,
)
from src.foundation.performance.domain.rules import assert_single_scope, next_revision
from src.foundation.performance.ports.repository import PerformanceRepository, StatementInputPort

logger = logging.getLogger(__name__)

_INSUFFICIENT_VALUATION_LIMITATION = (
    "IDENTITY_INSUFFICIENT_VALUATION_HISTORY: 기간 경계 평가액이 1개뿐이라 "
    "회계 항등식·TWR/MWR을 계산할 수 없습니다(추정치 아님, 미계산)."
)
_MISSING_LEDGER_LIMITATION = (
    "COMPONENTS_LEDGER_INCOMPLETE: fees/slippage/funding/fx/estimated_tax는 "
    "현재 원장에 컬럼이 없어 항상 PENDING입니다."
)


class MethodologyNotFoundError(Exception):
    def __init__(self, version: str) -> None:
        super().__init__(f"VALIDATION_METHODOLOGY_REQUIRED: {version}")
        self.reason_code = "VALIDATION_METHODOLOGY_REQUIRED"


def _signed_cashflow_total(cashflows: tuple[Cashflow, ...]) -> Decimal:
    total = Decimal(0)
    for cf in cashflows:
        total += cf.amount if cf.kind == CashflowKind.DEPOSIT else -cf.amount
    return total


def _gross_pnl(snapshots: tuple[ValuationSnapshot, ...]) -> Decimal | None:
    if not snapshots:
        return None
    latest = max(snapshots, key=lambda s: s.as_of)
    total = Decimal(0)
    for position in latest.positions:
        total += Decimal(str(position["realized_pnl"])) + Decimal(str(position["unrealized_pnl"]))
    return total


async def compute_statement(
    repo: PerformanceRepository,
    inputs: StatementInputPort,
    evidence_repo: AuditEventRepository | None,
    *,
    tenant_id: UUID,
    cmd: ComputeStatementCommand,
    trace_id: UUID,
    metrics: MetricsPort | None = None,
) -> PerformanceStatementView:
    # PLT-10 instrumentation point — defaults to NullMetrics, so existing callers
    # that don't pass `metrics` stay unaffected.
    metrics = metrics if metrics is not None else NullMetrics()
    started = time.monotonic()
    methodology_version = cmd.methodology_version or DEFAULT_METHODOLOGY.version
    try:
        methodology = await repo.get_methodology(methodology_version)
        if methodology is None:
            if methodology_version != DEFAULT_METHODOLOGY.version:
                raise MethodologyNotFoundError(methodology_version)
            methodology = await repo.insert_methodology(DEFAULT_METHODOLOGY)
    except MethodologyNotFoundError as exc:
        metrics.counter(PERFORMANCE_STATEMENT_COUNT_TOTAL, {"op": "compute", "outcome": "failed"})
        metrics.observe(
            PERFORMANCE_STATEMENT_DURATION_SECONDS,
            time.monotonic() - started,
            {"op": "compute", "outcome": "failed"},
        )
        logger.error(
            "performance_statement_compute_failed",
            extra={
                "event": "performance_statement_compute_failed",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "payload": {
                    "tenant_id": str(tenant_id),
                    "scope": cmd.scope.value,
                    "reason_code": exc.reason_code,
                },
            },
        )
        raise

    snapshots = await inputs.load_reconciled_snapshots(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    fills = await inputs.load_fills(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    cashflows = await inputs.load_cashflows(
        scope_ref=cmd.scope_ref, period_start=cmd.period_start, period_end=cmd.period_end
    )
    assert_single_scope((cmd.scope.value, *(s.scope for s in snapshots)))

    gross_pnl = _gross_pnl(snapshots)
    cashflows_net = _signed_cashflow_total(cashflows)
    components = ComponentBreakdown(
        gross_pnl=gross_pnl,
        fees=None,
        slippage=None,
        funding=None,
        fx=None,
        cashflows_net=cashflows_net,
        estimated_tax=None,
        net_pnl=None,
    )

    limitations = [_MISSING_LEDGER_LIMITATION]
    if len(snapshots) < 2:
        limitations.append(_INSUFFICIENT_VALUATION_LIMITATION)
    identity_ok = False
    identity_residual: Decimal | None = None

    input_refs = [f"snapshot:{s.id}" for s in snapshots]
    input_refs.extend(f"fill:{f['order_id']}" for f in fills)
    input_refs.append(f"trace:{trace_id}")

    prior = await repo.get_latest_statement(
        tenant_id=tenant_id,
        scope=cmd.scope.value,
        scope_ref=cmd.scope_ref,
        period_start=cmd.period_start,
        period_end=cmd.period_end,
        methodology_version=methodology_version,
    )
    revision_no = next_revision(prior.revision_no) if prior is not None else 1
    statement_id = uuid4()

    evidence_refs: tuple[str, ...] = ()
    if evidence_repo is not None:
        # The performance_statement WORM table has no UPDATE — inserting again
        # to populate evidence_refs after the initial insert would hit a
        # UNIQUE(revision_no) conflict. So we fix the statement_id first,
        # record the audit event *before* the statement insert, and insert only
        # once with that event.id in evidence_refs.
        event = await record_command_event(
            evidence_repo,
            tenant_id=tenant_id,
            aggregate_type="performance_statement",
            aggregate_id=statement_id,
            action="performance.statement_computed.v1",
            actor_subject_id=tenant_id,
            payload={
                "scope": cmd.scope.value,
                "scope_ref": cmd.scope_ref,
                "revision_no": revision_no,
                "trace_id": str(trace_id),
            },
        )
        evidence_refs = (f"audit:{event.id}",)

    statement = PerformanceStatement(
        id=statement_id,
        tenant_id=tenant_id,
        scope=cmd.scope.value,
        scope_ref=cmd.scope_ref,
        period_start=cmd.period_start,
        period_end=cmd.period_end,
        as_of=datetime.now(timezone.utc),
        methodology_version=methodology.version,
        methodology_hash=methodology.methodology_hash,
        input_refs=tuple(input_refs),
        components=components,
        returns=(
            ReturnFigure(
                value_pct=None,
                basis="NET",
                method="TWR",
                period_start=cmd.period_start,
                period_end=cmd.period_end,
                annualized=False,
                periods_per_year=methodology.periods_per_year,
            ),
        ),
        risk={"vol_pct": None, "mdd_pct": None, "sharpe": None, "calmar": None},
        benchmark=None,
        benchmark_ref=None,
        state=StatementState.ESTIMATED,
        revision_no=revision_no,
        prior_statement_id=prior.id if prior is not None else None,
        identity_ok=identity_ok,
        identity_residual=identity_residual,
        limitations=tuple(limitations),
        evidence_refs=evidence_refs,
    )

    saved = await repo.insert_statement(statement)
    elapsed = time.monotonic() - started
    metrics.counter(PERFORMANCE_STATEMENT_COUNT_TOTAL, {"op": "compute", "outcome": "completed"})
    metrics.observe(
        PERFORMANCE_STATEMENT_DURATION_SECONDS, elapsed, {"op": "compute", "outcome": "completed"}
    )
    logger.info(
        "performance_statement_computed",
        extra={
            "event": "performance_statement_computed",
            "duration_ms": round(elapsed * 1000),
            "payload": {
                "tenant_id": str(tenant_id),
                "statement_id": str(saved.id),
                "scope": cmd.scope.value,
                "revision_no": saved.revision_no,
            },
        },
    )
    return statement_to_view(saved)
