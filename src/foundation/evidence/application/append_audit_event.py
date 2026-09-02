"""AppendAuditEvent 커맨드.

Spec: AIOSproject 79번 §1/§2. FND-01/FND-02를 포함한 다른 bounded context가
고위험 커맨드 뒤에 이 함수를 호출해 감사 이벤트를 남긴다(아직 배선은
안 됐다 — 71번 §3 FND-03 자체 산출물은 "envelope + in-memory adapter"까지이고,
기존 컨텍스트에 실제로 연결하는 건 후속 리프).
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
    assert event.occurred_at is not None  # DB에서 온 이벤트는 항상 NOT NULL(마이그레이션 보장)
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
