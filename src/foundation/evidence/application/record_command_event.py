"""Public entry point that wires FND-03 to other bounded contexts.

Spec: Full-audit review (agent-platform-12, 2026-09-02) §6 — `append_audit_event()`
was already implemented (deliverable #71 §3 covers "envelope + in-memory adapter"),
yet callers remained at zero.

Call this function immediately after each context commits its own DB transaction
for state-changing commands (mandate activate/pause/resume, safety
control activate/deactivate, paper deployment request/start/pause/stop,
connection revoke, etc.). This module centralises the repetitive parts of
constructing a RecordAuditEventCommand (trace_id, default outcome/
classification), while each context supplies its own aggregate_type and action names.

Prior to PLT-07, `trace_id` was regenerated via `uuid4()` on every call,
breaking correlation (quoted from full-audit §6). Now it reuses the value from
the PLT-01 request context
(`src.core.observability.context.current()`) so that the `audit_event` row
written by this function shares the same `trace_id` as the `audit_log` row
recorded within the same request."""
from __future__ import annotations

from uuid import UUID

from src.core.observability.context import current as current_request_context
from src.foundation.evidence.application.append_audit_event import append_audit_event
from src.foundation.evidence.contracts.v1 import (
    AuditEventView,
    Classification,
    Outcome,
    RecordAuditEventCommand,
)
from src.foundation.evidence.ports.repository import AuditEventRepository


async def record_command_event(
    repo: AuditEventRepository,
    *,
    tenant_id: UUID | None,
    aggregate_type: str,
    aggregate_id: UUID,
    action: str,
    actor_subject_id: UUID | None,
    outcome: Outcome = Outcome.SUCCESS,
    classification: Classification = Classification.INTERNAL,
    aggregate_revision: int | None = None,
    payload: dict[str, object] | None = None,
) -> AuditEventView:
    """The `payload` must pass the safety check in rule #78 (AUD-004) — it must
    not contain secret-like fields (caller's responsibility; `append_audit_event`
    rejects violations with `UnsafePayloadError`)."""
    return await append_audit_event(
        repo,
        RecordAuditEventCommand(
            tenant_id=tenant_id,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_revision=aggregate_revision,
            action=action,
            outcome=outcome,
            actor_subject_id=actor_subject_id,
            trace_id=current_request_context().trace_id,
            payload=payload or {},
            classification=classification,
        ),
    )
