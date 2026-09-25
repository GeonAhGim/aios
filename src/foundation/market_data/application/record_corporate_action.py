"""LA-14 — Corporate action recording use case + 1:1 audit event.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5, §9.2 LA-14.

`ReferenceRepository.record_action`(LA-12) already handles `(instrument_id,
action_type, ex_date)` idempotency within its own transaction (resending
the same content does not create a new row but returns the existing value
unchanged). This function does not directly import the adapter's
`CorporateActionDigestMismatchError` (adapter-specific type) because the
domain/application layer must not know about adapters (71 §4) — instead,
it compares the existing row directly via `list_actions` **before** calling
`record_action`, and if the content differs, it rejects with this file's
own `CorporateActionConflictError` without invoking the adapter at all
(the adapter still prevents races — this pre-check exists to attach an
audit event, not to serve as the sole defense line).

Follows the `record_fill` convention exactly: a resend with identical
content (REPLAY) does not create an audit event (principle: "REPLAY does
not create an audit event either"). A resend with different content
(CONFLICT) follows the `post_entry` convention (commit a DENIED audit
then raise), but since this function opens its own transaction on its
own `pool` (it does not accept the caller's `conn`), the exception must
be raised **outside** the `async with` block to persist the DENIED row.
"""
from __future__ import annotations

from typing import Protocol
from uuid import UUID

import asyncpg

from src.foundation.evidence.api import (
    AuditEvent,
    Classification,
    Outcome,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.market_data.contracts.v1 import CorporateAction
from src.foundation.market_data.ports.reference_repository import ReferenceRepository

__all__ = ["AuditAppender", "CorporateActionConflictError", "record_corporate_action"]


class CorporateActionConflictError(Exception):
    """Resent with different ratio/cash_amount/source_ref for an existing
    row with the same `(instrument_id, action_type, ex_date)` — does not
    silently overwrite (fail-closed)."""

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
