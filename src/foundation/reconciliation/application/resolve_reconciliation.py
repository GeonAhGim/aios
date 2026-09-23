"""ResolveReconciliation command.

Spec: AIOSproject #80 §2 "RESOLVED opens recovery review; fresh trust/
policy/risk approval is required to run" / REC-007 "resolve alone cannot
resume; fresh trust/policy/risk/recovery approval required".

This command never touches safety_control (FND-06) — marking RESOLVED and
disarming the kill switch are two entirely separate actions. Disarming the
kill switch requires a separate call to risk_gate.deactivate_safety_control(),
and the actual resumption must then go through a full re-evaluation
(mandate+risk+connection) as required by paper_control.resume_deployment() —
the principle that "resolve alone" auto-resumes nothing is upheld across
independent contexts."""
from __future__ import annotations

from uuid import UUID

from src.foundation.reconciliation.contracts.v1 import Classification as ContractClassification
from src.foundation.reconciliation.contracts.v1 import ReconciliationStateView
from src.foundation.reconciliation.domain.models import Classification, ReconciliationState
from src.foundation.reconciliation.ports.repository import ReconciliationRepository

_RESOLVABLE_STATUSES = frozenset(
    {
        Classification.MATERIAL_MISMATCH,
        Classification.PROVIDER_UNAVAILABLE,
        Classification.INVESTIGATING,
    }
)


class ReconciliationStateNotFoundError(Exception):
    pass


class CrossTenantReconciliationAccessError(Exception):
    pass


class NotResolvableError(Exception):
    pass


def state_to_view(state: ReconciliationState) -> ReconciliationStateView:
    return ReconciliationStateView(
        target_ref=state.target_ref,
        target_type=state.target_type,
        aggregate_status=ContractClassification(state.aggregate_status.value),
        last_healthy_at=state.last_healthy_at,
        last_checked_at=state.last_checked_at,
        blocking_reason=state.blocking_reason,
        revision=state.revision,
    )


async def resolve_reconciliation(
    repo: ReconciliationRepository,
    *,
    tenant_id: UUID,
    actor_subject_id: UUID,
    target_ref: UUID,
    reason: str,
) -> ReconciliationStateView:
    state = await repo.get_state(target_ref)
    if state is None:
        raise ReconciliationStateNotFoundError(str(target_ref))
    if state.tenant_id != tenant_id:
        raise CrossTenantReconciliationAccessError(str(target_ref))
    if state.aggregate_status not in _RESOLVABLE_STATUSES:
        raise NotResolvableError(
            f"{state.aggregate_status.value} status is not a resolve target."
        )

    updated = await repo.transition_state_status(
        target_ref,
        expected_revision=state.revision,
        new_status=Classification.RESOLVED,
        blocking_reason=None,
        resolved_by=actor_subject_id,
        resolution_reason=reason,
    )
    return state_to_view(updated)
