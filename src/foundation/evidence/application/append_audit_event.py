"""AppendAuditEvent command.

Spec: AIOSproject #79 §1/§2. Other bounded contexts including FND-01/FND-02 call
this function after high-risk commands to record audit events (wiring is not yet
complete — #71 §3 FND-03 deliverable stops at "envelope + in-memory adapter";
actual integration into existing contexts is a follow-up leaf).
"""
from __future__ import annotations

from src.foundation.evidence.contracts.v1 import AuditEventView, RecordAuditEventCommand
from src.foundation.evidence.contracts.v1 import Classification as ContractClassification
from src.foundation.evidence.contracts.v1 import Outcome as ContractOutcome
from src.foundation.evidence.domain.models import AuditEvent
from src.foundation.evidence.domain.models import Classification as DomainClassification
from src.foundation.evidence.domain.models import Outcome as DomainOutcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.evidence.ports.repository import AuditEventRepository


def event_to_view(event: AuditEvent) -> AuditEventView:
    # Events from DB are always NOT NULL (guaranteed by migration)
    assert event.occurred_at is not None
    return AuditEventView(
        id=event.id,
        tenant_id=event.tenant_id,
        sequence_no=event.sequence_no,
        aggregate_type=event.aggregate_type,
        aggregate_id=event.aggregate_id,
        aggregate_revision=event.aggregate_revision,
        action=event.action,
        outcome=ContractOutcome(event.outcome.value),
        actor_subject_id=event.actor_subject_id,
        trace_id=event.trace_id,
        payload_hash=event.payload_hash,
        payload=event.payload,
        classification=ContractClassification(event.classification.value),
        previous_hash=event.previous_hash,
        event_hash=event.event_hash,
        occurred_at=event.occurred_at,
    )


async def append_audit_event(
    repo: AuditEventRepository, command: RecordAuditEventCommand
) -> AuditEventView:
    assert_safe_payload(command.payload)
    payload_hash = compute_payload_hash(command.payload)

    event = await repo.append_event(
        tenant_id=command.tenant_id,
        aggregate_type=command.aggregate_type,
        aggregate_id=command.aggregate_id,
        aggregate_revision=command.aggregate_revision,
        action=command.action,
        outcome=DomainOutcome(command.outcome.value),
        actor_subject_id=command.actor_subject_id,
        trace_id=command.trace_id,
        payload_hash=payload_hash,
        payload=command.payload,
        classification=DomainClassification(command.classification.value),
    )
    return event_to_view(event)
