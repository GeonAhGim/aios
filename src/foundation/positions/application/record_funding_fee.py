"""LB-13 — single path for funding-fee settlement -> position journal entry
(application/record_funding_fee).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9.3 LB-13.

Follows the same spirit as `record_fill` (LB-11, [[record_fill]]) — lock ->
read -> compute rule -> write -> audit — but is much simpler: funding never
touches cost-basis lots (`qty_delta=0`, [[journal_rules.funding_entry]]), so
it doesn't need `record_fill`'s upfront "skip the computation if this is an
idempotent re-submission" branch. Even if the same `funding_id` is resent,
recomputing `amount_base` has no side effect (no state change like lot
consumption), so the final decision still rests on the `sequence_no`
returned by `journal.append` (`sequence_no <= snapshot.last_journal_seq`
means it's already a folded REPLAY).

`RecordFundingCommand.amount` is the settlement amount the caller has
already computed ([[domain.funding_fees.funding_amount]] is the
responsibility of an upstream path (exchange funding collection, not yet
started) that produces this value, not this leaf's responsibility) — this
function only converts that amount to the base currency (delegating to
[[domain.fx.convert]]; it buys and re-declares the same shape as
`record_fill`'s `_fx_multiplier` for the same reason: module boundaries mean
private helpers aren't borrowed across modules) and writes it to the
journal. `RecordFundingCommand.rate` is not used in the computation (it's
already reflected in `amount`) — it is kept only in the audit payload for
root-cause tracing.

After the journal append, snapshot folding reuses
`snapshot_builder.apply_one` (LB-5, the sole "source of truth" computation
path) just like `record_fill` does.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass, Currency, FXRate, Money
from src.foundation.evidence.api import (
    AuditEvent,
    Classification,
    Outcome,
    assert_safe_payload,
    compute_payload_hash,
)
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
    PositionSnapshotView,
    RecordFundingCommand,
)
from src.foundation.positions.domain import fx, journal_rules
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — there is no `pos_snapshot` row matching
    `position_key` (same premise/re-declaration as
    [[record_fill.UnknownPositionError]]). Not retryable. This exception is
    also reused for the same reason as [[record_fill.UnknownPositionError]]
    when `command.tenant_id`/`account_id` differs from the actual owner
    (task-489/LB-18)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"알 수 없는 position_key(스냅샷 없음): {position_key!r}")
        self.position_key = position_key


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


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _fx_multiplier(
    amount: Money, base_currency: Currency, rate: FXRate | None
) -> tuple[Decimal | None, str | None]:
    """Same shape as [[record_fill._fx_multiplier]] (re-declared across the
    module boundary) — `(None, None)` when the currency matches."""
    if amount.currency == base_currency:
        return None, None
    converted = fx.convert(
        Money(amount=Decimal("1"), currency=amount.currency), base_currency, rate
    )
    assert converted.rate is not None
    return converted.rate.rate, converted.rate.source


async def record_funding_fee(
    conn: asyncpg.Connection,
    command: RecordFundingCommand,
    *,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    audit: AuditAppender,
    clock: Clock,
    fx_rate: FXRate | None = None,
) -> PositionSnapshotView:
    # FA-0d: same reason as [[record_fill]] — fail-closed format check (confirms it
    # went through the central constructor).
    PositionKey.parse(command.position_key)
    await _acquire_position_lock(conn, command.position_key)

    snapshot = await snapshots.get(conn, command.tenant_id, command.position_key)
    if snapshot is None or snapshot.account_id != command.account_id:
        raise UnknownPositionError(command.position_key)

    multiplier, fx_source = _fx_multiplier(command.amount, snapshot.base_currency, fx_rate)
    amount_base = command.amount.amount * (multiplier if multiplier is not None else Decimal("1"))

    entry_input = journal_rules.funding_entry(
        funding_id=command.funding_id,
        amount_base=amount_base,
        fx_rate=multiplier,
        fx_source=fx_source,
        occurred_at=command.occurred_at,
    )

    entry_view = await journal.append(
        conn,
        position_key=command.position_key,
        entry_type=JournalEntryType.FUNDING,
        qty_delta=entry_input.qty_delta,
        price=entry_input.price,
        fee=entry_input.fee,
        realized_pnl_base=entry_input.realized_pnl_base,
        fx_rate=entry_input.fx_rate,
        fx_source=entry_input.fx_source,
        source_event_type=entry_input.source_event_type,
        source_event_id=entry_input.source_event_id,
        idempotency_key=entry_input.idempotency_key,
        occurred_at=entry_input.occurred_at,
    )

    if entry_view.sequence_no <= snapshot.last_journal_seq:
        return snapshot

    fold_state = SnapshotFold(
        quantity=snapshot.quantity,
        lots=tuple(snapshot.lots),
        realized_pnl_base=snapshot.realized_pnl_base,
        fees_base=snapshot.fees_base,
        funding_base=snapshot.funding_base,
        last_journal_seq=snapshot.last_journal_seq,
    )
    new_fold = apply_one(
        fold_state,
        entry_view,
        position_key=command.position_key,
        cost_method=snapshot.cost_method,
        asset_class=asset_class,
    )

    new_snapshot = snapshot.model_copy(
        update={
            "quantity": new_fold.quantity,
            "avg_cost": Money(amount=new_fold.avg_cost, currency=snapshot.avg_cost.currency),
            "lots": list(new_fold.lots),
            "realized_pnl_base": new_fold.realized_pnl_base,
            "fees_base": new_fold.fees_base,
            "funding_base": new_fold.funding_base,
            "last_journal_seq": new_fold.last_journal_seq,
            "updated_at": clock(),
        }
    )
    persisted = await snapshots.upsert(conn, new_snapshot, expected_seq=snapshot.last_journal_seq)

    payload: dict[str, object] = {
        "position_key": command.position_key,
        "funding_id": command.funding_id,
        "entry_id": entry_view.id,
        "sequence_no": entry_view.sequence_no,
        "amount": str(command.amount.amount),
        "amount_ccy": command.amount.currency.value,
        "rate": str(command.rate),
        "amount_base": str(amount_base),
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=command.tenant_id,
        aggregate_type="pos_journal_entry",
        aggregate_id=command.account_id,
        aggregate_revision=entry_view.sequence_no,
        action="position.funding_recorded",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=command.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    return persisted
