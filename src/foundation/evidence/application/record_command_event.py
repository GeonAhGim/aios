"""FND-03을 다른 bounded context에 실제로 연결하는 공용 진입점.

Spec: 전수감사(agent-platform-12, 2026-09-02) §6 — append_audit_event()가
완성돼 있었지만(71번 §3 산출물은 "envelope + in-memory adapter"까지)
호출자가 지금까지 0이었다.

각 컨텍스트의 상태를 바꾸는 커맨드(mandate activate/pause/resume, safety
control activate/deactivate, paper deployment request/start/pause/stop,
connection revoke 등)가 자기 DB 트랜잭션을 커밋한 *직후* 이 함수를 호출한다
— RecordAuditEventCommand 조립의 반복되는 부분(trace_id, outcome/
classification 기본값)만 한 곳에 모으고, 실제 호출은 각 컨텍스트가
자기 aggregate_type/action 이름으로 한다.

trace_id는 PLT-07 이전에는 매 호출마다 `uuid4()`로 새로 만들어져
상관관계가 끊겼다(전수감사 §6 인용) — 이제 PLT-01 요청 컨텍스트
(`src.core.observability.context.current()`)의 값을 그대로 옮겨,
같은 요청에서 기록된 `audit_log` 행과 이 함수가 남기는 `audit_event`
행이 동일한 trace_id를 갖도록 한다."""
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
    """`payload`는 78번(AUD-004) 안전성 검사를 통과해야 한다 — secret류
    필드를 담지 않는다(호출부 책임, append_audit_event가 위반 시
    UnsafePayloadError로 거부)."""
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
