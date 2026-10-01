"""LB-11 — the single path for recording a Fill into the position journal (application/record_fill).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9.3 LB-11.

The order follows the same spirit as LC-9 `post_entry.py`: lock -> idempotency pre-check ->
rule computation -> write -> audit. However `PositionJournalRepository.append` (LB-9) already
does the `position_key`-scoped advisory lock and idempotency determination inside its own
transaction (see the port docstring); the reason this function also acquires the lock on the
**same namespace, same key** up front is that the cost-basis (FIFO/WEIGHTED) calculation must
read state "after the lock" — if the snapshot were read before locking (a concurrent fill could
commit in between), realized P&L would be computed on stale lots, a race condition. It is safe
for `journal.append` to request the same lock again, because within the same transaction it
returns immediately (reentrant).

For idempotent re-submission, this function first checks whether `pos_journal.idempotency_key`
already has the same key (EXISTS, O(1) thanks to the UNIQUE index) and skips the cost-basis
calculation itself — applying the same fill again on top of already-consumed lots could
incorrectly raise `NegativeQuantityError` even though it is genuinely idempotent (a resend must
succeed). The final determination is still made from the `sequence_no` that `journal.append`
returns — if `sequence_no <= snapshot.last_journal_seq` the entry was already folded (REPLAY),
otherwise (`== last_journal_seq + 1`) it is a new entry. REPLAY produces neither a snapshot
upsert nor an audit event (same principle as post_entry's "a digest-matching REPLAY leaves no
audit trail either" — an audit event is always exactly one per journal entry, 1:1).
If the `idempotency_key` matches but the content differs (`POS_IDEMPOTENCY_DIGEST_MISMATCH`),
`journal.append` raises and this function propagates it as-is — it is a caller bug, not
retryable, and no audit is left (this taxonomy tier is "not retryable", so §4.3 does not
require a DENIED audit — unlike domain C's ledger postings, where "no posting without an
audit event" applies, domain B carries no such requirement).

The cost-basis and journal-entry rules themselves are not reimplemented here: lot seeding keeps
the same shape as `snapshot_builder._seeded_cost_basis` in this file too (module boundaries mean
a private helper is not imported across modules), but the actual cost-basis computation is done
by `cost_basis.selector.cost_basis_for` + `FifoLots/WeightedAverage.apply` (LB-2/LB-3), and
journal-entry input assembly is done by `journal_rules.fill_entry` (LB-5). After the new entry is
persisted, folding the snapshot reuses `snapshot_builder.apply_one` (LB-5) as-is — this looks like
computing the cost basis twice (once here to obtain `realized_pnl_base`, and again when
`apply_one` folds the journal entry), but both runs are deterministic over the same lots and the
same input, so there is no drift — this is a deliberate choice so as not to violate the §4.3
"snapshot = fold(journal)" invariant that `apply_one` is the sole "source of truth" computation
path for the actual snapshot.

`asset_class` is not part of `RecordFillCommand` (the contract) — `pos_account`/`pos_snapshot`
also have no place to store it yet (the instrument_ref table work hasn't started; see the LB-8
migration comment). So this function takes it as a keyword argument using a value the caller
already knows (the order's `asset_class`) — LB-12 passes through the existing `order.asset_class`.

If the price/fee currency differs from the account's base currency, `fx_rate` (an `FXRate` the
caller looked up beforehand) must be supplied — no substituting 0 (`fx.FxRateMissingError`,
retryable). If the currency is the same, `fx_rate` can be omitted.
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
    Lot,
    PositionSnapshotView,
    RecordFillCommand,
)
from src.foundation.positions.domain import fx, journal_rules
from src.foundation.positions.domain.cost_basis.fifo import FifoLots, FillEvent
from src.foundation.positions.domain.cost_basis.selector import cost_basis_for
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, apply_one
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — there is no `pos_snapshot` row matching this
    `position_key`. This function follows the same LB-9 premise as the rest of the
    pipeline: something outside LB-11 must open the position before a journal append —
    not retryable.

    This exception is also reused when `command.tenant_id`/`account_id` differs from
    the actual owner (fix for a real defect surfaced by the task-489/LB-18
    cross_tenant adversarial test — no new error code is introduced). Letting the
    caller distinguish "exists but belongs to someone else" from "doesn't exist at
    all" would turn this into an oracle leaking whether someone else's position_key
    exists, so the two cases are deliberately merged into one exception."""

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


def _seed_basis(
    template: FifoLots | WeightedAverage, lots: tuple[Lot, ...]
) -> FifoLots | WeightedAverage:
    """Seed the cost-basis engine with an empty queue or a single lot (same shape as
    `snapshot_builder._seeded_cost_basis` — a deliberate re-declaration so that a
    private helper isn't borrowed across a module boundary; the actual cost-basis
    logic itself is not here)."""
    if isinstance(template, FifoLots):
        return FifoLots(lots)
    assert isinstance(template, WeightedAverage)
    return WeightedAverage(lots[0] if lots else None)


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _fx_multiplier(
    price: Money, base_currency: Currency, rate: FXRate | None
) -> tuple[Decimal | None, str | None]:
    """Derive a single price-currency -> base-currency multiplier. Same currency
    returns `(None, None)` (no fx_rate on the journal row means "already base
    currency", the convention shared by `journal_rules`/`snapshot_builder`). This
    reuses `fx.convert(amount=1, ...)` just to extract the multiplier — direction
    (forward/inverse conversion) is delegated to `fx.convert` and not reimplemented
    here."""
    if price.currency == base_currency:
        return None, None
    converted = fx.convert(Money(amount=Decimal("1"), currency=price.currency), base_currency, rate)
    assert converted.rate is not None
    return converted.rate.rate, converted.rate.source


async def record_fill(
    conn: asyncpg.Connection,
    command: RecordFillCommand,
    *,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    audit: AuditAppender,
    clock: Clock,
    fx_rate: FXRate | None = None,
) -> PositionSnapshotView:
    # FA-0d: position_key must be a value built via domain.position_key.PositionKey
    # (the central constructor) — parse() here confirms the format so that
    # legacy/malformed keys assembled via f-string/concat can't enter the journal;
    # this blocks them fail-closed (not retryable, caller bug).
    PositionKey.parse(command.position_key)
    await _acquire_position_lock(conn, command.position_key)

    snapshot = await snapshots.get(conn, command.tenant_id, command.position_key)
    if snapshot is None or snapshot.account_id != command.account_id:
        raise UnknownPositionError(command.position_key)

    idempotency_key = f"fill:{command.order_id}:{command.fill_seq}"
    # Calling `journal.list_for` (an O(n) scan of the full journal for this
    # position_key) just to decide whether to skip cost-basis recomputation would be
    # overkill — `pos_journal` has a UNIQUE index on `idempotency_key`, so this uses
    # EXISTS for an O(1) check (§8.4 round-trip reduction, task-653). The final
    # new-vs-REPLAY determination is still made from the `sequence_no` that
    # `journal.append` returns (below) — this EXISTS only decides "is it OK to
    # recompute the cost basis".
    is_replay_candidate = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM pos_journal WHERE idempotency_key = $1)",
        idempotency_key,
    )

    if is_replay_candidate:
        realized_pnl_base = Decimal("0")
        fx_rate_value: Decimal | None = None
        fx_source_value: str | None = None
    else:
        template = cost_basis_for(snapshot.cost_method, asset_class)
        basis = _seed_basis(template, tuple(snapshot.lots))
        fill = FillEvent(
            side=command.side,
            quantity=command.quantity,
            price=command.price.amount,
            occurred_at=command.occurred_at,
        )
        result = basis.apply(fill)
        multiplier, fx_source_value = _fx_multiplier(command.price, snapshot.base_currency, fx_rate)
        raw_realized = result.realized_pnl * command.contract_multiplier
        realized_pnl_base = raw_realized * (multiplier if multiplier is not None else Decimal("1"))
        fx_rate_value = multiplier

    entry_input = journal_rules.fill_entry(
        order_id=command.order_id,
        fill_seq=command.fill_seq,
        side=command.side,
        quantity=command.quantity,
        price=command.price,
        fee=command.fee,
        realized_pnl_base=realized_pnl_base,
        fx_rate=fx_rate_value,
        fx_source=fx_source_value,
        occurred_at=command.occurred_at,
    )

    entry_view = await journal.append(
        conn,
        position_key=command.position_key,
        entry_type=JournalEntryType.FILL,
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
        "order_id": str(command.order_id),
        "fill_seq": command.fill_seq,
        "entry_id": entry_view.id,
        "sequence_no": entry_view.sequence_no,
        "qty_delta": str(entry_view.qty_delta),
        "realized_pnl_base": str(entry_view.realized_pnl_base),
    }
    assert_safe_payload(payload)
    await audit.append_event_in(
        conn,
        tenant_id=command.tenant_id,
        aggregate_type="pos_journal_entry",
        aggregate_id=command.order_id,
        aggregate_revision=command.fill_seq,
        action="position.fill_recorded",
        outcome=Outcome.SUCCESS,
        actor_subject_id=None,
        trace_id=command.trace_id,
        payload_hash=compute_payload_hash(payload),
        payload=payload,
        classification=Classification.INTERNAL,
    )

    return persisted
