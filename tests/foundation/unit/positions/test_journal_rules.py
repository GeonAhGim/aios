"""LB-5 — journal_rules 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-5,
`unit/positions/test_journal_rules.py` DoD("seq 건너뜀 거부, 체인 재계산
불일치 감지, 변조 1비트 감지").
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView
from src.foundation.positions.domain.journal_rules import (
    ChainIntegrityError,
    JournalEntryInput,
    SequenceConflictError,
    digest_for,
    entry_hash_for,
    fee_entry,
    fill_entry,
    funding_entry,
    validate_sequence,
    verify_chain,
)

_ORDER_ID = UUID("00000000-0000-0000-0000-000000000001")


def _now(offset_seconds: int = 0) -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def _money(amount: str, currency: Currency = Currency.USDT) -> Money:
    return Money(amount=Decimal(amount), currency=currency)


# ---- fill_entry / funding_entry / fee_entry ----------------------------------


def test_fill_entry_buy_has_positive_qty_delta_and_fill_idempotency_key() -> None:
    entry = fill_entry(
        order_id=_ORDER_ID,
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=_money("100"),
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        occurred_at=_now(),
    )
    assert entry.entry_type is JournalEntryType.FILL
    assert entry.qty_delta == Decimal("10")
    assert entry.idempotency_key == f"fill:{_ORDER_ID}:1"
    assert entry.source_event_id == f"{_ORDER_ID}:1"


def test_fill_entry_sell_has_negative_qty_delta() -> None:
    entry = fill_entry(
        order_id=_ORDER_ID,
        fill_seq=2,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=_money("110"),
        fee=None,
        realized_pnl_base=Decimal("40"),
        fx_rate=None,
        fx_source=None,
        occurred_at=_now(),
    )
    assert entry.qty_delta == Decimal("-4")
    assert entry.realized_pnl_base == Decimal("40")


def test_fill_entry_rejects_non_positive_quantity() -> None:
    with pytest.raises(ValueError):
        fill_entry(
            order_id=_ORDER_ID,
            fill_seq=1,
            side=OrderSide.BUY,
            quantity=Decimal("0"),
            price=_money("100"),
            fee=None,
            realized_pnl_base=Decimal("0"),
            fx_rate=None,
            fx_source=None,
            occurred_at=_now(),
        )


def test_funding_entry_has_zero_qty_delta_and_funding_idempotency_key() -> None:
    entry = funding_entry(
        funding_id="fnd-1", amount_base=Decimal("-5.5"), occurred_at=_now()
    )
    assert entry.entry_type is JournalEntryType.FUNDING
    assert entry.qty_delta == Decimal("0")
    assert entry.realized_pnl_base == Decimal("-5.5")
    assert entry.idempotency_key == "funding:fnd-1"
    assert entry.price is None


def test_fee_entry_has_zero_qty_delta_zero_realized_and_fee_idempotency_key() -> None:
    entry = fee_entry(source_event_id="wd-1", fee=_money("1.5"), occurred_at=_now())
    assert entry.entry_type is JournalEntryType.FEE
    assert entry.qty_delta == Decimal("0")
    assert entry.realized_pnl_base == Decimal("0")
    assert entry.fee == _money("1.5")
    assert entry.idempotency_key == "fee:wd-1"


def test_replaying_same_fill_inputs_produces_identical_digest() -> None:
    kwargs = dict(
        order_id=_ORDER_ID,
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=_money("100"),
        fee=_money("0.5"),
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        occurred_at=_now(),
    )
    first = fill_entry(**kwargs)  # type: ignore[arg-type]
    second = fill_entry(**kwargs)  # type: ignore[arg-type]
    assert first.digest == second.digest


def test_digest_changes_when_qty_delta_differs() -> None:
    base = digest_for(Decimal("10"), _money("100"), None, _now())
    tampered = digest_for(Decimal("11"), _money("100"), None, _now())
    assert base != tampered


# ---- validate_sequence --------------------------------------------------------


def test_validate_sequence_accepts_next_contiguous_seq() -> None:
    validate_sequence("pk", prev_seq=3, new_seq=4)  # no raise


def test_validate_sequence_rejects_gap() -> None:
    with pytest.raises(SequenceConflictError) as exc_info:
        validate_sequence("pk", prev_seq=3, new_seq=6)
    assert exc_info.value.expected == 4
    assert exc_info.value.actual == 6


def test_validate_sequence_rejects_swapped_order() -> None:
    with pytest.raises(SequenceConflictError):
        validate_sequence("pk", prev_seq=5, new_seq=4)


# ---- entry_hash_for / verify_chain --------------------------------------------


def _entry(
    seq: int,
    prev_hash: str | None,
    *,
    entry_type: JournalEntryType = JournalEntryType.FILL,
    qty_delta: Decimal = Decimal("1"),
    occurred_at: datetime | None = None,
) -> PositionJournalEntryView:
    at = occurred_at if occurred_at is not None else _now(seq)
    price = _money("100") if entry_type is JournalEntryType.FILL else None
    digest = digest_for(qty_delta, price, None, at)
    entry_hash = entry_hash_for(prev_hash, seq, entry_type, digest, at)
    return PositionJournalEntryView(
        id=seq,
        position_key="pk",
        sequence_no=seq,
        entry_type=entry_type,
        qty_delta=qty_delta,
        price=price,
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        source_event_type="fill",
        source_event_id=f"src:{seq}",
        idempotency_key=f"idem:{seq}",
        prev_hash=prev_hash,
        entry_hash=entry_hash,
        occurred_at=at,
        recorded_at=at,
    )


def _valid_chain(n: int) -> list[PositionJournalEntryView]:
    entries: list[PositionJournalEntryView] = []
    prev_hash: str | None = None
    for seq in range(1, n + 1):
        entry = _entry(seq, prev_hash)
        entries.append(entry)
        prev_hash = entry.entry_hash
    return entries


def test_verify_chain_accepts_untampered_chain() -> None:
    verify_chain("pk", _valid_chain(5))  # no raise


def test_verify_chain_detects_broken_prev_hash_link() -> None:
    entries = _valid_chain(3)
    tampered = entries[1].model_copy(update={"prev_hash": "not-the-real-prev-hash"})
    entries[1] = tampered
    with pytest.raises(ChainIntegrityError):
        verify_chain("pk", entries)


def test_verify_chain_detects_single_bit_tamper_in_qty_delta() -> None:
    entries = _valid_chain(3)
    # entry_hash는 그대로 두고 qty_delta만 바꾼다 — digest 재계산이 어긋나
    # entry_hash 불일치로 이어져야 한다.
    entries[2] = entries[2].model_copy(update={"qty_delta": entries[2].qty_delta + Decimal("1")})
    with pytest.raises(ChainIntegrityError):
        verify_chain("pk", entries)


def test_journal_entry_input_is_frozen() -> None:
    entry = fee_entry(source_event_id="wd-1", fee=_money("1"), occurred_at=_now())
    assert isinstance(entry, JournalEntryInput)
    with pytest.raises(AttributeError):
        entry.qty_delta = Decimal("999")  # type: ignore[misc]
