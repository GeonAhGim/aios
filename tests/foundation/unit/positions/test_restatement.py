"""FA-11 — restatement 단위 테스트.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-11.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from src.core.bitemporal import BitemporalRecord
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import CostMethod, PositionJournalEntryView
from src.foundation.positions.domain.journal_rules import (
    JournalEntryInput,
    SequenceConflictError,
    fill_entry,
)
from src.foundation.positions.domain.restatement import (
    NotRetroactiveError,
    RecordAlreadyClosedError,
    is_retroactive,
    restate_position,
)
from src.foundation.positions.domain.snapshot_builder import SnapshotFold, fold

_POSITION_KEY = "binance:BTCUSDT:s1:e1"
_ORDER_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def _to_view(seq: int, entry: JournalEntryInput) -> PositionJournalEntryView:
    return PositionJournalEntryView(
        id=seq,
        position_key=_POSITION_KEY,
        sequence_no=seq,
        entry_type=entry.entry_type,
        qty_delta=entry.qty_delta,
        price=entry.price,
        fee=entry.fee,
        realized_pnl_base=entry.realized_pnl_base,
        fx_rate=entry.fx_rate,
        fx_source=entry.fx_source,
        source_event_type=entry.source_event_type,
        source_event_id=entry.source_event_id,
        idempotency_key=entry.idempotency_key,
        prev_hash=None,
        entry_hash=f"hash-{seq}",
        occurred_at=entry.occurred_at,
        recorded_at=entry.occurred_at,
    )


def _fill_view(
    seq: int, side: OrderSide, quantity: str, price: str, *, occurred_at: datetime
) -> PositionJournalEntryView:
    entry = fill_entry(
        order_id=_ORDER_ID,
        fill_seq=seq,
        side=side,
        quantity=Decimal(quantity),
        price=Money(amount=Decimal(price), currency=Currency.USDT),
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        occurred_at=occurred_at,
    )
    return _to_view(seq, entry)


def _fold(entries: list[PositionJournalEntryView]) -> SnapshotFold:
    return fold(
        entries,
        position_key=_POSITION_KEY,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )


def _prior_record(
    entries: list[PositionJournalEntryView],
    *,
    valid_from: datetime,
    tx_from: datetime,
    tx_to: datetime | None = None,
) -> BitemporalRecord[SnapshotFold]:
    return BitemporalRecord(
        value=_fold(entries), valid_from=valid_from, valid_to=None, tx_from=tx_from, tx_to=tx_to
    )


_ENTRY_1 = _fill_view(1, OrderSide.BUY, "10", "100", occurred_at=_dt(2026, 9, 3))
_ENTRY_2 = _fill_view(2, OrderSide.BUY, "5", "110", occurred_at=_dt(2026, 9, 4))
_LATE_ENTRY = _fill_view(3, OrderSide.BUY, "2", "90", occurred_at=_dt(2026, 9, 1))


def test_is_retroactive_true_when_occurred_before_valid_from() -> None:
    assert is_retroactive(_LATE_ENTRY, _dt(2026, 9, 4)) is True


def test_is_retroactive_false_when_occurred_after_valid_from() -> None:
    on_time_entry = _fill_view(3, OrderSide.BUY, "2", "90", occurred_at=_dt(2026, 9, 5))
    assert is_retroactive(on_time_entry, _dt(2026, 9, 4)) is False


def test_restate_position_folds_late_entry_onto_prior() -> None:
    prior = _prior_record([_ENTRY_1, _ENTRY_2], valid_from=_dt(2026, 9, 4), tx_from=_dt(2026, 9, 4))
    now = _dt(2026, 9, 10)

    result = restate_position(
        position_key=_POSITION_KEY,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
        prior=prior,
        late_entry=_LATE_ENTRY,
        now=now,
    )

    assert result.restated_current.value.quantity == Decimal("17")
    assert result.restated_current.value.last_journal_seq == 3
    assert result.restated_current.valid_from == _LATE_ENTRY.occurred_at
    assert result.restated_current.tx_from == now
    assert result.restated_current.tx_to is None


def test_restate_position_closes_prior_at_now_without_mutating_its_value() -> None:
    prior = _prior_record([_ENTRY_1, _ENTRY_2], valid_from=_dt(2026, 9, 4), tx_from=_dt(2026, 9, 4))
    now = _dt(2026, 9, 10)

    result = restate_position(
        position_key=_POSITION_KEY,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
        prior=prior,
        late_entry=_LATE_ENTRY,
        now=now,
    )

    assert result.closed_prior.tx_to == now
    assert result.closed_prior.value == prior.value
    assert prior.tx_to is None  # 원본 레코드는 불변(frozen dataclass) — in-place 변경 없음


def test_restate_position_rejects_non_retroactive_entry() -> None:
    prior = _prior_record([_ENTRY_1, _ENTRY_2], valid_from=_dt(2026, 9, 4), tx_from=_dt(2026, 9, 4))
    on_time_entry = _fill_view(3, OrderSide.BUY, "2", "90", occurred_at=_dt(2026, 9, 5))

    with pytest.raises(NotRetroactiveError):
        restate_position(
            position_key=_POSITION_KEY,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
            prior=prior,
            late_entry=on_time_entry,
            now=_dt(2026, 9, 10),
        )


def test_restate_position_rejects_already_closed_prior() -> None:
    prior = _prior_record(
        [_ENTRY_1, _ENTRY_2],
        valid_from=_dt(2026, 9, 4),
        tx_from=_dt(2026, 9, 4),
        tx_to=_dt(2026, 9, 6),
    )

    with pytest.raises(RecordAlreadyClosedError):
        restate_position(
            position_key=_POSITION_KEY,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
            prior=prior,
            late_entry=_LATE_ENTRY,
            now=_dt(2026, 9, 10),
        )


def test_restate_position_rejects_sequence_gap() -> None:
    prior = _prior_record([_ENTRY_1, _ENTRY_2], valid_from=_dt(2026, 9, 4), tx_from=_dt(2026, 9, 4))
    gapped_entry = _fill_view(5, OrderSide.BUY, "2", "90", occurred_at=_dt(2026, 9, 1))

    with pytest.raises(SequenceConflictError):
        restate_position(
            position_key=_POSITION_KEY,
            cost_method=CostMethod.FIFO,
            asset_class=AssetClass.CRYPTO,
            prior=prior,
            late_entry=gapped_entry,
            now=_dt(2026, 9, 10),
        )
