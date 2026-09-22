"""LB-5 — Fill/funding-fee/fee events → `pos_journal` entry conversion rules (journal_rules).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §5, §9 LB-5.

The three factories (`fill_entry`/`funding_entry`/`fee_entry`) produce
`JournalEntryInput` objects that can be passed directly to
[[ports/journal_repository.PositionJournalRepository.append]] —
`sequence_no`/`id`/`prev_hash`/`entry_hash`/`recorded_at` are filled by the
adapter (LB-9) under advisory lock, so they are absent from this leaf's
output. `digest` implements verbatim the formula from the §5 journal append
table ("digest = sha256(qty_delta, price, fee, occurred_at)") —
`realized_pnl_base` is intentionally excluded (replay detection only needs to
check whether the same event produced the same fill/fee; comparing derived PnL
as well increases the false-positive surface area even for `Decimal` which has
no floating-point rounding).

The rule for folding `fee` (original currency) into the base currency is shared
between this module and [[snapshot_builder]]: if `fx_rate` is present,
`fee.amount * fx_rate`, otherwise (already in base currency) `fee.amount` as-is
— a design decision of this leaf to store only the multiplier, not the full
`fx.FXRate` (base/quote pair) from LB-4 in the journal row (LB-8 schema has
`fee_ccy` but no `fee_base`). The amounts received by `funding_entry`/`fee_entry`
are assumed to already be converted to base currency by this multiplier —
currency-rate lookup itself is the responsibility of [[fx.convert]]/
[[funding_fees.to_base]] (LB-4) and this module does not call them.

Pure domain (0 DB/HTTP imports) — timestamps are always passed as arguments.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.data.models.base import Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
    PositionErrorCode,
    PositionJournalEntryView,
)


class SequenceConflictError(Exception):
    """`POS_SEQUENCE_CONFLICT` — §4.3 "(position_key, sequence_no) unique &
    contiguous (starting from 1)" violation. Retriable (re-query then retry)."""

    code = PositionErrorCode.SEQUENCE_CONFLICT

    def __init__(self, position_key: str, expected: int, actual: int) -> None:
        super().__init__(
            f"{position_key}: sequence_no should be {expected} but got {actual}"
        )
        self.position_key = position_key
        self.expected = expected
        self.actual = actual


def validate_sequence(position_key: str, prev_seq: int, new_seq: int) -> None:
    """§4.3 `new.seq == prev.seq + 1`. `prev_seq=0` means the journal is empty
    (first entry gets `sequence_no=1`)."""
    if new_seq != prev_seq + 1:
        raise SequenceConflictError(position_key, prev_seq + 1, new_seq)


def _money_token(m: Money | None) -> str:
    return f"{m.amount}:{m.currency.value}" if m is not None else ""


def digest_for(
    qty_delta: Decimal, price: Money | None, fee: Money | None, occurred_at: datetime
) -> str:
    """§5 journal append idempotency: `digest = sha256(qty_delta, price, fee,
    occurred_at)`. A replay of the same `idempotency_key` with a different value
    raises `POS_IDEMPOTENCY_DIGEST_MISMATCH` (adapter's LB-9 responsibility)."""
    payload = "|".join(
        [str(qty_delta), _money_token(price), _money_token(fee), occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def entry_hash_for(
    prev_hash: str | None,
    sequence_no: int,
    entry_type: JournalEntryType,
    digest: str,
    occurred_at: datetime,
) -> str:
    """One link in the chain (same pattern as LC-3 `hash_chain.entry_hash`). When
    `prev_hash` is `None` (global first entry), treat as empty string to start
    deterministically."""
    payload = "|".join(
        [prev_hash or "", str(sequence_no), entry_type.value, digest, occurred_at.isoformat()]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChainIntegrityError(Exception):
    """Journal hash chain break or suspected tampering (`prev_hash` mismatch, or
    `entry_hash` recomputed from fields differs from stored value). LB-1
    taxonomy has no `POS_*` code for this exact case (closest is
    `POS_NAV_CHAIN_BROKEN`, which is NAV-only) — raise without code mapping,
    following the precedent of
    [[cost_basis.selector.UnknownAssetClassError]]."""

    def __init__(self, position_key: str, sequence_no: int, reason: str) -> None:
        super().__init__(f"{position_key} seq={sequence_no}: {reason}")
        self.position_key = position_key
        self.sequence_no = sequence_no


def verify_chain(position_key: str, entries: Sequence[PositionJournalEntryView]) -> None:
    """`sequence_no` 오름차순으로 정렬된 저널 목록의 해시 체인을 검증한다.
    문제 없으면 조용히 반환하고, 있으면 `ChainIntegrityError`를 던진다."""
    expected_prev: str | None = None
    for entry in entries:
        if entry.prev_hash != expected_prev:
            raise ChainIntegrityError(
                position_key,
                entry.sequence_no,
                "prev_hash가 이전 엔트리의 entry_hash와 일치하지 않습니다(체인 단절 또는 변조).",
            )
        digest = digest_for(entry.qty_delta, entry.price, entry.fee, entry.occurred_at)
        recomputed = entry_hash_for(
            entry.prev_hash, entry.sequence_no, entry.entry_type, digest, entry.occurred_at
        )
        if recomputed != entry.entry_hash:
            raise ChainIntegrityError(
                position_key,
                entry.sequence_no,
                "entry_hash differs from recomputed value (content tampering suspected).",
            )
        expected_prev = entry.entry_hash


@dataclass(frozen=True, slots=True)
class JournalEntryInput:
    """Input passed directly to `PositionJournalRepository.append` (LB-7).
    `digest` is used by the adapter for replay detection (stored column but not
    exposed in LB-1 view — see §5)."""

    entry_type: JournalEntryType
    qty_delta: Decimal
    price: Money | None
    fee: Money | None
    realized_pnl_base: Decimal
    fx_rate: Decimal | None
    fx_source: str | None
    source_event_type: str
    source_event_id: str
    idempotency_key: str
    occurred_at: datetime
    digest: str


def fill_entry(
    *,
    order_id: UUID,
    fill_seq: int,
    side: OrderSide,
    quantity: Decimal,
    price: Money,
    fee: Money | None,
    realized_pnl_base: Decimal,
    fx_rate: Decimal | None,
    fx_source: str | None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """One fill → `FILL` entry. Idempotency key is `f"fill:{order_id}:{fill_seq}"`
    (same scheme as §3.2 `RecordFillCommand` contract). `realized_pnl_base` is
    passed by the caller (LB-11 `record_fill`) already converted via cost method
    (selector path FIFO/WEIGHTED) and base currency — this function does not
    perform cost calculations."""
    if quantity <= 0:
        raise ValueError(f"quantity must be positive: {quantity}")
    qty_delta = quantity if side is OrderSide.BUY else -quantity
    return JournalEntryInput(
        entry_type=JournalEntryType.FILL,
        qty_delta=qty_delta,
        price=price,
        fee=fee,
        realized_pnl_base=realized_pnl_base,
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="fill",
        source_event_id=f"{order_id}:{fill_seq}",
        idempotency_key=f"fill:{order_id}:{fill_seq}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, price, fee, occurred_at),
    )


def funding_entry(
    *,
    funding_id: str,
    amount_base: Decimal,
    fx_rate: Decimal | None = None,
    fx_source: str | None = None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """One funding PnL settlement → `FUNDING` entry. Quantity does not change
    (`qty_delta=0`). Idempotency key is `f"funding:{funding_id}"` (same as
    §3.2 `RecordFundingCommand`). `amount_base` is accumulated as `funding_base`
    in [[snapshot_builder.apply_one]] (§4.3 does not provide a dedicated
    `funding_base` column in `pos_journal`, so the `realized_pnl_base` column is
    reused — routed by entry_type)."""
    qty_delta = Decimal("0")
    return JournalEntryInput(
        entry_type=JournalEntryType.FUNDING,
        qty_delta=qty_delta,
        price=None,
        fee=None,
        realized_pnl_base=amount_base,
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="funding",
        source_event_id=funding_id,
        idempotency_key=f"funding:{funding_id}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, None, None, occurred_at),
    )


def fee_entry(
    *,
    source_event_id: str,
    fee: Money,
    fx_rate: Decimal | None = None,
    fx_source: str | None = None,
    occurred_at: datetime,
) -> JournalEntryInput:
    """Standalone fee not attached to a fill (e.g., withdrawal fee) → `FEE`
    entry. Neither quantity nor realized PnL changes. Idempotency key is
    `f"fee:{source_event_id}"`."""
    qty_delta = Decimal("0")
    return JournalEntryInput(
        entry_type=JournalEntryType.FEE,
        qty_delta=qty_delta,
        price=None,
        fee=fee,
        realized_pnl_base=Decimal("0"),
        fx_rate=fx_rate,
        fx_source=fx_source,
        source_event_type="fee",
        source_event_id=source_event_id,
        idempotency_key=f"fee:{source_event_id}",
        occurred_at=occurred_at,
        digest=digest_for(qty_delta, None, fee, occurred_at),
    )
