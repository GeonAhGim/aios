"""LA-14 — 기업행위(corporate action) 기록 유스케이스 + 감사 이벤트 1:1.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5, §9.2 LA-14.

`ReferenceRepository.record_action`(LA-12)이 이미 `(instrument_id,
action_type, ex_date)` 멱등을 자신의 트랜잭션 안에서 처리한다(같은 내용
재전송은 새 행을 만들지 않고 기존 값을 그대로 반환). 이 함수가 그 어댑터의
`CorporateActionDigestMismatchError`(어댑터 전용 타입)를 직접 import해
잡지 않는 이유는 domain/application이 adapters를 몰라야 하기 때문이다
(71번 §4) — 대신 `record_action`을 부르기 **전에** `list_actions`로 기존
행을 직접 비교해 내용이 다르면 어댑터를 아예 호출하지 않고 이 파일
소유의 `CorporateActionConflictError`로 거부한다(레이스는 어댑터가 여전히
막는다 — 이 사전 비교는 감사 이벤트를 붙이기 위한 것이지 유일한 방어선이
아니다).

`record_fill`의 관례를 그대로 따른다: 내용이 같은 재전송(REPLAY)은 감사
이벤트를 만들지 않는다("REPLAY는 감사 이벤트도 만들지 않는다" 원칙). 내용이
다른 재전송(CONFLICT)은 `post_entry`의 관례(DENIED 감사를 커밋한 뒤 예외를
던짐)를 따르되, 이 함수는 자체 `pool`에서 트랜잭션을 열므로(호출자 `conn`을
받지 않음) DENIED 행을 살리려면 예외를 `async with` 블록 **밖**에서 던진다.
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = ["AuditAppender", "CorporateActionConflictError", "record_corporate_action"]


class CorporateActionConflictError(Exception):
    """`(instrument_id, action_type, ex_date)`가 같은 기존 행과 ratio/
    cash_amount/source_ref가 달라 재전송됨 — 조용히 덮지 않는다(fail-closed)."""

    def __init__(self, action: CorporateAction) -> None:
        super().__init__(
            f"다른 내용으로 재전송됨: instrument_id={action.instrument_id} "
            f"action_type={action.action_type} ex_date={action.ex_date}"
        )
        self.action = action


class AuditAppender(Protocol):
    async def append_event_in(
        self,
        conn: asyncpg.Connection,
        *,
        tenant_id: UUID | None,
        aggregate_type: str,
        aggregate_id: UUID,
        aggregate_revision: int | None,
        action: str,
        outcome: Outcome,
        actor_subject_id: UUID | None,
        trace_id: UUID,
        payload_hash: str,
        payload: dict[str, object],
        classification: Classification,
    ) -> AuditEvent: ...


def _same_content(existing: CorporateAction, incoming: CorporateAction) -> bool:
    return (existing.ratio, existing.cash_amount, existing.source_ref) == (
        incoming.ratio,
        incoming.cash_amount,
        incoming.source_ref,
    )


async def record_corporate_action(
    pool: asyncpg.Pool,
    action: CorporateAction,
    *,
    actor_subject_id: UUID,
    trace_id: UUID,
    refs: ReferenceRepository,
    audit: AuditAppender,
) -> CorporateAction:
    conflict: CorporateActionConflictError | None = None
    recorded = action

    async with pool.acquire() as conn, conn.transaction():
        prior = next(
            (
                a
                for a in await refs.list_actions(conn, action.instrument_id)
                if a.action_type == action.action_type and a.ex_date == action.ex_date
            ),
            None,
        )

        if prior is not None and not _same_content(prior, action):
            conflict = CorporateActionConflictError(action)
            payload: dict[str, object] = {
                "instrument_id": str(action.instrument_id),
                "action_type": action.action_type,
                "ex_date": action.ex_date.isoformat(),
                "existing_source_ref": prior.source_ref,
                "rejected_source_ref": action.source_ref,
            }
            assert_safe_payload(payload)
            await audit.append_event_in(
                conn,
                tenant_id=None,
                aggregate_type="md_instrument",
                aggregate_id=action.instrument_id,
                aggregate_revision=None,
                action="instrument.corporate_action_recorded",
                outcome=Outcome.DENIED,
                actor_subject_id=actor_subject_id,
                trace_id=trace_id,
                payload_hash=compute_payload_hash(payload),
                payload=payload,
                classification=Classification.INTERNAL,
            )
        else:
            recorded = await refs.record_action(conn, action)
            if prior is None:
                payload = {
                    "instrument_id": str(action.instrument_id),
                    "action_type": action.action_type,
                    "ex_date": action.ex_date.isoformat(),
                    "ratio": str(action.ratio),
                    "source_ref": action.source_ref,
                }
                assert_safe_payload(payload)
                await audit.append_event_in(
                    conn,
                    tenant_id=None,
                    aggregate_type="md_instrument",
                    aggregate_id=action.instrument_id,
                    aggregate_revision=None,
                    action="instrument.corporate_action_recorded",
                    outcome=Outcome.SUCCESS,
                    actor_subject_id=actor_subject_id,
                    trace_id=trace_id,
                    payload_hash=compute_payload_hash(payload),
                    payload=payload,
                    classification=Classification.INTERNAL,
                )

    if conflict is not None:
        raise conflict

    return recorded
