"""전이 이벤트 → 감사 기록 브리지(L4 명세 §2-C, §9 L4-07).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C
`application/audit_bridge.py`, §9 L4-07.

`order_repository.transition()`과 같은 `conn`(같은 트랜잭션) 안에서 호출된다
— 이 함수는 스스로 커넥션을 얻거나 트랜잭션을 열지 않는다(105번 §5.1과 동일
계약). 두 곳에 같은 전이를 남기는 이유가 다르다: `record_audit_log`(07번
§7.4 `audit_log`)는 운영자가 보는 사람 친화적 로그, evidence
`append_event_in`(79번 §1 해시 체인, L0-4)은 위변조 불가 체인 증거다.

`PostgresAuditEventRepository.append_event_in`은 `self._pool`을 절대
참조하지 않는다(소스 확인, `foundation/evidence/adapters/postgres_repository.py`)
— `foundation/ledger/adapters/legacy_wallet_bridge.py`와 동일한 이유로
placeholder pool을 넣어도 안전하다. 이 전제가 깨지면(그 메서드가 실제로
`self._pool`을 쓰게 리팩터되면) 여기도 진짜 pool을 넘기게 바꿔야 한다.

tenant_id: `OrderTransitionEvent`엔 tenant 정보가 없다(§2-C 시그니처가
`emit(conn, ev, *, trace_id)`로 고정) — evidence 체인은 system 체인
(`tenant_id=None`)에 기록한다. tenant별 체인이 필요해지면 시그니처 확장이
필요하다(이 리프 스코프 밖).
"""
from __future__ import annotations

from typing import cast
from uuid import UUID

import asyncpg

from src.core.logging.audit_log import record_audit_log
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload
from src.services.oms.contracts.v1_events import OrderTransitionEvent

_UNUSED_POOL = cast(asyncpg.Pool, None)  # 위 docstring 참조 — 실제로 참조되지 않는다.
_audit = PostgresAuditEventRepository(_UNUSED_POOL)


def _actor_uuid(ev: OrderTransitionEvent) -> UUID | None:
    return ev.actor_subject_id if isinstance(ev.actor_subject_id, UUID) else None


async def emit(conn: asyncpg.Connection, ev: OrderTransitionEvent, *, trace_id: UUID) -> None:
    payload: dict[str, object] = {
        "order_id": str(ev.order_id),
        "from_status": ev.from_status.value,
        "to_status": ev.to_status.value,
        "event": ev.event,
        "reason_code": ev.reason_code,
    }
    assert_safe_payload(payload)

    await record_audit_log(
        conn,
        actor_agent="oms.order_repository",
        action_type=ev.event,
        target_type="order",
        target_id=str(ev.order_id),
        decision_data=payload,
        trace_id=trace_id,
    )
    await _audit.append_event_in(
        conn,
        tenant_id=None,
        aggregate_type="oms_order",
        aggregate_id=ev.order_id,
        aggregate_revision=ev.seq,
        action=ev.event,
        outcome=Outcome.SUCCESS,
        actor_subject_id=_actor_uuid(ev),
        trace_id=trace_id,
        payload_hash=ev.payload_hash,
        payload=payload,
        classification=Classification.INTERNAL,
    )
