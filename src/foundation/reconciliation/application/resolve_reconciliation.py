"""ResolveReconciliation 커맨드.

Spec: AIOSproject 80번 §2 "RESOLVED opens recovery review; fresh trust/
policy/risk approval is required to run" / REC-007 "resolve alone cannot
resume; fresh trust/policy/risk/recovery approval required".

이 커맨드는 safety_control(FND-06)을 절대 건드리지 않는다 — RESOLVED로
표시하는 것과 kill switch를 해제하는 것은 완전히 다른 두 행위다. kill
switch 해제는 risk_gate.deactivate_safety_control()을 별도로 호출해야
하고, 그 뒤 실제 재개는 paper_control.resume_deployment()가 요구하는
완전한 재평가(mandate+risk+connection)를 다시 거쳐야 한다 — "resolve
alone"이 그 무엇도 자동으로 재개시키지 않는다는 원칙을 이 세 컨텍스트가
서로 독립적이라는 구조 자체로 강제한다."""
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
            f"{state.aggregate_status.value} 상태는 resolve 대상이 아닙니다."
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
