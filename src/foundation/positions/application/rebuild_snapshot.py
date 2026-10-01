"""LB-13 — Operational tool to rebuild the snapshot by folding the entire journal
(application/rebuild_snapshot).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9 LB-13,
§4.3 invariant "snapshot = fold(journal)".

Unlike `record_fill`/`record_funding_fee`(LB-11/13), this function does not
accept an already-open transaction `conn` — it opens its own transaction
because the function itself is a self-contained operational task (the reason
the signature in §9 table takes `pool` instead of `conn`). It acquires a
`position_key` advisory lock (same namespace as [[record_fill]]) and reads
while preventing `record_fill`/`record_funding_fee` from appending new
entries for the same position — eliminating the race condition that would
arise without the lock between "the point of reading the full journal" and
"the point of writing the fold result" (new entries arriving in between
would not be reflected in the rebuild result, or the rebuild would
overwrite new entries).

`pos_journal` is never written to (WORM) — this leaf touches only the
`pos_snapshot` table, and even then only via conditional UPDATE on
`snapshots.upsert` (`expected_seq`). When `dry_run=True` (default), it
reports drift without writing — following the §9 DoD ("rebuild drift ∅")
intent that operators first verify drift via dry-run before applying it
for real. When there is no drift, it does not write even if `dry_run=False`
(to avoid unnecessary writes and `updated_at` refresh).

`asset_class` is an argument for the same reason as [[record_fill]] — there
is no place to store it in `pos_snapshot`, so the caller must pass it
(not absent from the abbreviated signature in §9 table, but following the
precedent that `record_fill` already broke for the same reason).

Cost-method recalculation and funding/fee accumulation rules are not
re-implemented — `snapshot_builder.fold`(LB-5, `functools.reduce(apply_one,
...)`) is the sole "truth computation" path.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg

from src.data.models.base import AssetClass, Money
from src.foundation.positions.contracts.v1 import PositionSnapshotView, RebuildReport
from src.foundation.positions.domain.position_key import PositionKey
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, fold
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

Clock = Callable[[], datetime]

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """`POS_ACCOUNT_UNKNOWN` — no `pos_snapshot` row corresponds to the
    `position_key`. Journal entries alone cannot restore account static
    context such as `tenant_id`/`account_id`/`instrument_id`/`base_currency`
    ([[snapshot_builder.SnapshotFold]] docstring), so rebuild also assumes
    an existing snapshot row. This exception is also reused when the
    `tenant_id` passed by the caller differs from the actual owner
    (same reason as [[record_fill.UnknownPositionError]], task-489/LB-18 —
    `SnapshotRepository.get` became tenant-scoped, imposing a new premise
    that the caller must know the `tenant_id`)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"알 수 없는 position_key(스냅샷 없음): {position_key!r}")
        self.position_key = position_key


async def _acquire_position_lock(conn: asyncpg.Connection, position_key: str) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
        _LOCK_NAMESPACE,
        position_key,
    )


def _drift(
    current: PositionSnapshotView, folded: SnapshotFold
) -> dict[str, tuple[Decimal, Decimal]]:
    candidates: dict[str, tuple[Decimal, Decimal]] = {
        "quantity": (current.quantity, folded.quantity),
        "avg_cost": (current.avg_cost.amount, folded.avg_cost),
        "realized_pnl_base": (current.realized_pnl_base, folded.realized_pnl_base),
        "fees_base": (current.fees_base, folded.fees_base),
        "funding_base": (current.funding_base, folded.funding_base),
    }
    return {name: pair for name, pair in candidates.items() if pair[0] != pair[1]}


async def rebuild_snapshot(
    position_key: str,
    *,
    tenant_id: UUID,
    asset_class: AssetClass,
    journal: PositionJournalRepository,
    snapshots: SnapshotRepository,
    pool: asyncpg.Pool,
    clock: Clock,
    dry_run: bool = True,
) -> RebuildReport:
    # FA-0d: same reason as [[record_fill]] — fail-closed format check (confirms
    # it went through the central constructor) — the rebuild target key is no exception.
    PositionKey.parse(position_key)
    async with pool.acquire() as conn, conn.transaction():
        await _acquire_position_lock(conn, position_key)

        snapshot = await snapshots.get(conn, tenant_id, position_key)
        if snapshot is None:
            raise UnknownPositionError(position_key)

        entries = await journal.list_for(conn, position_key)
        folded = fold(
            entries,
            position_key=position_key,
            cost_method=snapshot.cost_method,
            asset_class=asset_class,
        )
        drift = _drift(snapshot, folded)

        if dry_run or not drift:
            return RebuildReport(
                position_key=position_key, entries=len(entries), drift=drift, applied=False
            )

        rebuilt = snapshot.model_copy(
            update={
                "quantity": folded.quantity,
                "avg_cost": Money(amount=folded.avg_cost, currency=snapshot.avg_cost.currency),
                "lots": list(folded.lots),
                "realized_pnl_base": folded.realized_pnl_base,
                "fees_base": folded.fees_base,
                "funding_base": folded.funding_base,
                "last_journal_seq": folded.last_journal_seq,
                "updated_at": clock(),
            }
        )
        await snapshots.upsert(conn, rebuilt, expected_seq=snapshot.last_journal_seq)

        return RebuildReport(
            position_key=position_key, entries=len(entries), drift=drift, applied=True
        )
