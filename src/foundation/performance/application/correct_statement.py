"""CorrectStatement command — creates a `CORRECTED` revision (prior ref, delta, reason).

Spec: docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2.6/§9(L49), #81
§2 "If upstream correction occurs, create CORRECTED revision with prior
ref, delta, reason ... never rewrite statement history."

Original rows are never modified (WORM) — this command appends a new row
that inherits the latest revision's values and records the `reason` in an
audit event and in `limitations`. Re-fetching inputs and re-computing
(economics delta) is out of scope for this leaf; calling the router that
invokes compute_statement.py to produce a new revision for the same period
is the caller's responsibility. This command only handles the administrative
procedure of "marking the result as CORRECTED and linking it to the prior
revision" (100-line cap, SCAFFOLD)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.ports.repository import AuditEventRepository
from src.foundation.performance.application.statement_projection import statement_to_view
from src.foundation.performance.contracts.v1 import PerformanceStatementView
from src.foundation.performance.domain.models import StatementState
from src.foundation.performance.domain.rules import next_revision
from src.foundation.performance.ports.repository import PerformanceRepository


class StatementNotFoundError(Exception):
    pass


class CrossTenantStatementAccessError(Exception):
    """Error taxonomy `AUTH_PERFORMANCE_SCOPE_DENIED` (#72) — the caller maps
    this to 403 (same principle as get_statement.py; #81 §3 names this
    class separately from 404)."""

    def __init__(self, statement_id: UUID) -> None:
        super().__init__(f"AUTH_PERFORMANCE_SCOPE_DENIED: {statement_id}")
        self.reason_code = "AUTH_PERFORMANCE_SCOPE_DENIED"


async def correct_statement(
    repo: PerformanceRepository,
    evidence_repo: AuditEventRepository | None,
    *,
    tenant_id: UUID,
    statement_id: UUID,
    reason: str,
    trace_id: UUID,
) -> PerformanceStatementView:
    original = await repo.get_statement(statement_id)
    if original is None:
        raise StatementNotFoundError(str(statement_id))
    if original.tenant_id != tenant_id:
        raise CrossTenantStatementAccessError(statement_id)

    latest = await repo.get_latest_statement(
        tenant_id=tenant_id,
        scope=original.scope,
        scope_ref=original.scope_ref,
        period_start=original.period_start,
        period_end=original.period_end,
        methodology_version=original.methodology_version,
    ) or original

    reason_note = f"CORRECTED(prior={latest.id}): {reason}"
    corrected_id = uuid4()

    evidence_refs = latest.evidence_refs
    if evidence_repo is not None:
        event = await record_command_event(
            evidence_repo,
            tenant_id=tenant_id,
            aggregate_type="performance_statement",
            aggregate_id=corrected_id,
            action="performance.statement_corrected.v1",
            actor_subject_id=tenant_id,
            payload={
                "prior_statement_id": str(latest.id),
                "reason": reason,
                "trace_id": str(trace_id),
            },
        )
        evidence_refs = (*latest.evidence_refs, f"audit:{event.id}")

    corrected = replace(
        latest,
        id=corrected_id,
        as_of=datetime.now(timezone.utc),
        state=StatementState.CORRECTED,
        revision_no=next_revision(latest.revision_no),
        prior_statement_id=latest.id,
        limitations=(*latest.limitations, reason_note),
        evidence_refs=evidence_refs,
    )
    saved = await repo.insert_statement(corrected)
    return statement_to_view(saved)
