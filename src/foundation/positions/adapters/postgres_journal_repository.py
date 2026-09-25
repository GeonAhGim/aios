"""LB-9 — asyncpg implementation of `PositionJournalRepository` (ports/journal_repository.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-9.

`append()` serialises per position_key using the 2-arg form of
`pg_advisory_xact_lock(hashtext('pos_journal'), hashtext(position_key))`
because `pos_journal` must be unique per `(position_key, sequence_no)` tuple
(different from the global single chain of LC-8b `ledger_journal_entry`),
as shown in §5 table.

`pos_journal.tenant_id`/`account_id` are NOT NULL but absent from the
`append()` signature in `ports/journal_repository.py` (this leaf does not
change the port). The only valid interpretation is the assumption that a
`pos_snapshot` row (§4.3 "snapshot = fold(journal)") already exists before
the first `append()` — the caller (LB-11 `record_fill`, not yet implemented)
creates an empty snapshot (quantity=0, last_journal_seq=0) via
`SnapshotRepository.upsert` with `tenant_id`/`account_id`/`instrument_id`
when opening a position for the first time, and only then calls
`journal.append()`. If the snapshot is missing, raise
`UnknownPositionError` (POS_ACCOUNT_UNKNOWN, non-retryable) — fail-closed.

`digest` (§5: `sha256(qty_delta, price, fee, occurred_at)`) is for
retransmission detection only; `entry_hash`/`prev_hash` form a tamper-detection
chain on the journal row itself — a relationship analogous to LC-8b
`hash_chain.py` (two hashes with different purposes) reimplemented in this
file (no domain module exists for this purpose under LB-1~7 — the §9 LB-9
table lists only 3 adapters as this leaf's deliverable).
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from typing import Any

import asyncpg
from pydantic import AwareDatetime

from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView

_LOCK_NAMESPACE = "pos_journal"


class UnknownPositionError(Exception):
    """No `pos_snapshot` row corresponds to `position_key` — the position
    must be opened before appending to journal (POS_ACCOUNT_UNKNOWN, non-retryable)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(f"Unknown position_key (no snapshot): {position_key!r}")
        self.position_key = position_key


class IdempotencyDigestMismatchError(Exception):
    """The same `idempotency_key` was retransmitted with different content
    (non-retryable, POS_IDEMPOTENCY_DIGEST_MISMATCH)."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency_key={key!r}: retransmission digest differs from existing.")
        self.key = key


def _digest(
    qty_delta: Decimal, price: Money | None, fee: Money | None, occurred_at: AwareDatetime
) -> str:
    canonical = {
        "qty_delta": str(qty_delta),
        "price": None if price is None else [str(price.amount), price.currency.value],
        "fee": None if fee is None else [str(fee.amount), fee.currency.value],
        "occurred_at": occurred_at.isoformat(),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode("utf-8")).hexdigest()


def _entry_hash(
    prev: str | None,
    position_key: str,
    seq: int,
    entry_type: JournalEntryType,
    digest: str,
    occurred_at: AwareDatetime,
) -> str:
    payload = "|".join(
        [prev or "", position_key, str(seq), entry_type.value, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _row_to_view(row: asyncpg.Record | dict[str, Any]) -> PositionJournalEntryView:
    price = (
        None
        if row["price"] is None
        else Money(amount=row["price"], currency=Currency(row["price_ccy"]))
    )
    fee = (
        None if row["fee"] is None else Money(amount=row["fee"], currency=Currency(row["fee_ccy"]))
    )
    return PositionJournalEntryView(
        id=row["id"],
        position_key=row["position_key"],
        sequence_no=row["sequence_no"],
        entry_type=JournalEntryType(row["entry_type"]),
        qty_delta=row["qty_delta"],
        price=price,
        fee=fee,
        realized_pnl_base=row["realized_pnl_base"],
        fx_rate=row["fx_rate"],
        fx_source=row["fx_source"],
        source_event_type=row["source_event_type"],
        source_event_id=row["source_event_id"],
        idempotency_key=row["idempotency_key"],
        prev_hash=row["prev_hash"],
        entry_hash=row["entry_hash"],
        occurred_at=row["occurred_at"],
        recorded_at=row["recorded_at"],
    )


class PostgresJournalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def append(
        self,
        conn: asyncpg.Connection,
        *,
        position_key: str,
        entry_type: JournalEntryType,
        qty_delta: Decimal,
        price: Money | None,
        fee: Money | None,
        realized_pnl_base: Decimal,
        fx_rate: Decimal | None,
        fx_source: str | None,
        source_event_type: str,
        source_event_id: str,
        idempotency_key: str,
        occurred_at: AwareDatetime,
    ) -> PositionJournalEntryView:
        # Keep the lock as a standalone round-trip. Attempting to fold it into
        # the FROM/JOIN clause of the integrated SELECT below would cause PG
        # to evaluate the FROM clause before the lock function (reading
        # last_entry before acquiring the lock), which actually reproduced a
        # (position_key, sequence_no) UNIQUE violation under 20-way concurrent
        # append (task-653, empirically measured) — do not revert.
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtext($1), hashtext($2))",
            _LOCK_NAMESPACE,
            position_key,
        )

        new_digest = _digest(qty_delta, price, fee, occurred_at)

        # Combine idempotency lookup + snapshot owner (tenant/account) lookup +
        # previous row (sequence_no, entry_hash) lookup into a single LEFT JOIN,
        # reducing 3 round-trips to 1. Even if the snapshot is missing (new
        # position_key), last_entry is always empty so the logic is unaffected.
        combined = await conn.fetchrow(
            "SELECT "
            " snap.tenant_id AS snap_tenant_id, snap.account_id AS snap_account_id, "
            " last_entry.sequence_no AS last_sequence_no, "
            " last_entry.entry_hash AS last_entry_hash, "
            " existing.id AS existing_id, existing.position_key AS existing_position_key, "
            " existing.sequence_no AS existing_sequence_no, "
            " existing.entry_type AS existing_entry_type, "
            " existing.qty_delta AS existing_qty_delta, existing.price AS existing_price, "
            " existing.price_ccy AS existing_price_ccy, existing.fee AS existing_fee, "
            " existing.fee_ccy AS existing_fee_ccy, "
            " existing.realized_pnl_base AS existing_realized_pnl_base, "
            " existing.fx_rate AS existing_fx_rate, existing.fx_source AS existing_fx_source, "
            " existing.source_event_type AS existing_source_event_type, "
            " existing.source_event_id AS existing_source_event_id, "
            " existing.idempotency_key AS existing_idempotency_key, "
            " existing.digest AS existing_digest, existing.prev_hash AS existing_prev_hash, "
            " existing.entry_hash AS existing_entry_hash, "
            " existing.occurred_at AS existing_occurred_at, "
            " existing.recorded_at AS existing_recorded_at "
            "FROM (SELECT $2::varchar AS position_key) AS target "
            "LEFT JOIN pos_snapshot snap ON snap.position_key = target.position_key "
            "LEFT JOIN LATERAL ("
            "  SELECT sequence_no, entry_hash FROM pos_journal "
            "  WHERE position_key = target.position_key "
            "  ORDER BY sequence_no DESC LIMIT 1"
            ") last_entry ON true "
            "LEFT JOIN pos_journal existing ON existing.idempotency_key = $1",
            idempotency_key,
            position_key,
        )
        assert combined is not None

        if combined["existing_id"] is not None:
            if combined["existing_digest"] != new_digest:
                raise IdempotencyDigestMismatchError(idempotency_key)
            return _row_to_view(
                {
                    "id": combined["existing_id"],
                    "position_key": combined["existing_position_key"],
                    "sequence_no": combined["existing_sequence_no"],
                    "entry_type": combined["existing_entry_type"],
                    "qty_delta": combined["existing_qty_delta"],
                    "price": combined["existing_price"],
                    "price_ccy": combined["existing_price_ccy"],
                    "fee": combined["existing_fee"],
                    "fee_ccy": combined["existing_fee_ccy"],
                    "realized_pnl_base": combined["existing_realized_pnl_base"],
                    "fx_rate": combined["existing_fx_rate"],
                    "fx_source": combined["existing_fx_source"],
                    "source_event_type": combined["existing_source_event_type"],
                    "source_event_id": combined["existing_source_event_id"],
                    "idempotency_key": combined["existing_idempotency_key"],
                    "prev_hash": combined["existing_prev_hash"],
                    "entry_hash": combined["existing_entry_hash"],
                    "occurred_at": combined["existing_occurred_at"],
                    "recorded_at": combined["existing_recorded_at"],
                }
            )

        if combined["snap_tenant_id"] is None:
            raise UnknownPositionError(position_key)

        last_sequence_no = combined["last_sequence_no"]
        next_seq = 1 if last_sequence_no is None else last_sequence_no + 1
        prev_hash: str | None = combined["last_entry_hash"]
        new_hash = _entry_hash(
            prev_hash, position_key, next_seq, entry_type, new_digest, occurred_at
        )

        row = await conn.fetchrow(
            "INSERT INTO pos_journal "
            "(tenant_id, account_id, position_key, sequence_no, entry_type, qty_delta, "
            " price, price_ccy, fee, fee_ccy, realized_pnl_base, fx_rate, fx_source, "
            " source_event_type, source_event_id, idempotency_key, digest, prev_hash, "
            " entry_hash, occurred_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20) "
            "RETURNING *",
            combined["snap_tenant_id"],
            combined["snap_account_id"],
            position_key,
            next_seq,
            entry_type.value,
            qty_delta,
            None if price is None else price.amount,
            None if price is None else price.currency.value,
            None if fee is None else fee.amount,
            None if fee is None else fee.currency.value,
            realized_pnl_base,
            fx_rate,
            fx_source,
            source_event_type,
            source_event_id,
            idempotency_key,
            new_digest,
            prev_hash,
            new_hash,
            occurred_at,
        )
        return _row_to_view(row)

    async def list_for(
        self, conn: asyncpg.Connection, position_key: str, from_seq: int = 0
    ) -> list[PositionJournalEntryView]:
        rows = await conn.fetch(
            "SELECT * FROM pos_journal WHERE position_key = $1 AND sequence_no > $2 "
            "ORDER BY sequence_no ASC",
            position_key,
            from_seq,
        )
        return [_row_to_view(row) for row in rows]

    async def last(
        self, conn: asyncpg.Connection, position_key: str
    ) -> PositionJournalEntryView | None:
        row = await conn.fetchrow(
            "SELECT * FROM pos_journal WHERE position_key = $1 ORDER BY sequence_no DESC LIMIT 1",
            position_key,
        )
        return None if row is None else _row_to_view(row)
