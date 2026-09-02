"""LB-5 — snapshot_builder 단위 테스트(fold 등가성 property 포함).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-5,
`unit/positions/test_snapshot_builder.py` DoD("property: 임의 체결열(시드
랜덤 200열)에서 fold(all) == reduce(apply_one); 재빌드 결정론").
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from functools import reduce
from uuid import UUID

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    JournalEntryType,
    PositionJournalEntryView,
)
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.journal_rules import (
    JournalEntryInput,
    SequenceConflictError,
    fee_entry,
    fill_entry,
    funding_entry,
)
from src.foundation.positions.domain.snapshot_builder import (
    SnapshotFold,
    UnsupportedEntryTypeError,
    apply_one,
    fold,
)

_POSITION_KEY = "binance:BTCUSDT:s1:e1"
_ORDER_ID = UUID("00000000-0000-0000-0000-0000000000aa")
_BASE_TIME = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _to_view(seq: int, entry: JournalEntryInput, prev_hash: str | None) -> PositionJournalEntryView:
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
        prev_hash=prev_hash,
        entry_hash=f"hash-{seq}",
        occurred_at=entry.occurred_at,
        recorded_at=entry.occurred_at,
    )


def _fill_view(
    sequence_no: int,
    side: OrderSide,
    quantity: str,
    price: str,
    *,
    fee: Money | None = None,
    realized_pnl_base: str = "0",
    occurred_at: datetime | None = None,
) -> PositionJournalEntryView:
    """`sequence_no`는 §4.3대로 1부터 시작; `prev_hash`는 자동으로 앞 엔트리에 잇는다."""
    at = occurred_at if occurred_at is not None else _BASE_TIME + timedelta(seconds=sequence_no)
    entry = fill_entry(
        order_id=_ORDER_ID,
        fill_seq=sequence_no,
        side=side,
        quantity=Decimal(quantity),
        price=Money(amount=Decimal(price), currency=Currency.USDT),
        fee=fee,
        realized_pnl_base=Decimal(realized_pnl_base),
        fx_rate=None,
        fx_source=None,
        occurred_at=at,
    )
    prev_hash = f"hash-{sequence_no - 1}" if sequence_no > 1 else None
    return _to_view(sequence_no, entry, prev_hash)


def _bare_view(**overrides: object) -> PositionJournalEntryView:
    """필드 전부를 무해한 기본값으로 채우고 필요한 것만 덮어쓴다(기형 엔트리용)."""
    defaults: dict[str, object] = dict(
        id=1,
        position_key=_POSITION_KEY,
        sequence_no=1,
        entry_type=JournalEntryType.FILL,
        qty_delta=Decimal("1"),
        price=None,
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        source_event_type="fill",
        source_event_id="src-1",
        idempotency_key="idem-1",
        prev_hash=None,
        entry_hash="hash-1",
        occurred_at=_BASE_TIME,
        recorded_at=_BASE_TIME,
    )
    defaults.update(overrides)
    return PositionJournalEntryView(**defaults)  # type: ignore[arg-type]


def _random_entries(seed: int, n: int) -> list[PositionJournalEntryView]:
    """결정적 무작위 체결/펀딩/수수료 열 — 러닝 수량을 추적해 매도가 보유량을
    넘지 않게 한다(초과매도 거부는 별도 negative test가 담당)."""
    rng = random.Random(seed)
    running_qty = Decimal("0")
    entries: list[PositionJournalEntryView] = []
    prev_hash: str | None = None
    at = _BASE_TIME

    for i in range(n):
        at = at + timedelta(seconds=1)
        kind = rng.choice(["fill", "fill", "fill", "funding", "fee"])
        if kind == "fill":
            can_sell = running_qty > 0 and rng.random() < 0.5
            if can_sell:
                side = OrderSide.SELL
                qty = min(Decimal(rng.randint(1, max(int(running_qty), 1))), running_qty)
            else:
                side = OrderSide.BUY
                qty = Decimal(rng.randint(1, 20))
            price = Money(amount=Decimal(rng.randint(50, 500)), currency=Currency.USDT)
            fee = (
                Money(amount=Decimal(rng.randint(0, 5)), currency=Currency.USDT)
                if rng.random() < 0.3
                else None
            )
            input_ = fill_entry(
                order_id=_ORDER_ID,
                fill_seq=i,
                side=side,
                quantity=qty,
                price=price,
                fee=fee,
                realized_pnl_base=(
                    Decimal(rng.randint(-10, 10)) if side is OrderSide.SELL else Decimal("0")
                ),
                fx_rate=None,
                fx_source=None,
                occurred_at=at,
            )
            running_qty = running_qty + input_.qty_delta
        elif kind == "funding":
            input_ = funding_entry(
                funding_id=f"fnd-{i}", amount_base=Decimal(rng.randint(-5, 5)), occurred_at=at
            )
        else:
            input_ = fee_entry(
                source_event_id=f"fee-{i}",
                fee=Money(amount=Decimal(rng.randint(1, 3)), currency=Currency.USDT),
                occurred_at=at,
            )

        view = _to_view(i + 1, input_, prev_hash)
        entries.append(view)
        prev_hash = view.entry_hash

    return entries


def _apply(state: SnapshotFold, entry: PositionJournalEntryView) -> SnapshotFold:
    return apply_one(
        state,
        entry,
        position_key=_POSITION_KEY,
        cost_method=CostMethod.FIFO,
        asset_class=AssetClass.CRYPTO,
    )


def _fold(
    entries: list[PositionJournalEntryView],
    *,
    asset_class: AssetClass = AssetClass.CRYPTO,
    initial: SnapshotFold | None = None,
) -> SnapshotFold:
    return fold(
        entries,
        position_key=_POSITION_KEY,
        cost_method=CostMethod.FIFO,
        asset_class=asset_class,
        initial=initial,
    )


def test_fold_equals_manual_reduce_over_200_random_entries() -> None:
    entries = _random_entries(seed=42, n=200)

    via_fold = _fold(entries)
    via_reduce = reduce(_apply, entries, SnapshotFold())

    assert via_fold == via_reduce


def test_fold_is_deterministic_across_repeated_runs() -> None:
    entries = _random_entries(seed=7, n=200)

    assert _fold(entries) == _fold(entries)


def test_fold_is_associative_when_chunked() -> None:
    entries = _random_entries(seed=99, n=200)
    midpoint = 100

    whole = _fold(entries)
    first_half = _fold(entries[:midpoint])
    chunked = _fold(entries[midpoint:], initial=first_half)

    assert whole == chunked


def test_fold_buy_then_partial_sell_matches_fifo_arithmetic() -> None:
    entries = [
        _fill_view(1, OrderSide.BUY, "10", "100"),
        _fill_view(2, OrderSide.SELL, "4", "120", realized_pnl_base="80"),
    ]

    result = _fold(entries)

    assert result.quantity == Decimal("6")
    assert result.avg_cost == Decimal("100")
    assert result.realized_pnl_base == Decimal("80")
    assert result.last_journal_seq == 2


def test_fold_forces_weighted_average_for_derivative_asset_class() -> None:
    entries = [
        _fill_view(1, OrderSide.BUY, "2", "100"),
        _fill_view(2, OrderSide.BUY, "2", "200"),
    ]

    result = _fold(entries, asset_class=AssetClass.KR_FUTURES)

    # 파생상품은 FIFO를 요청해도 selector가 WEIGHTED를 강제하므로 로트가
    # 여러 개 쌓이지 않고 단일 블렌디드 평단(150)으로 뭉친다.
    assert result.quantity == Decimal("4")
    assert result.avg_cost == Decimal("150")
    assert len(result.lots) == 1


def test_apply_one_rejects_oversell() -> None:
    entries = [
        _fill_view(1, OrderSide.BUY, "10", "100"),
        _fill_view(2, OrderSide.SELL, "11", "120"),
    ]

    with pytest.raises(NegativeQuantityError):
        _fold(entries)


def test_apply_one_rejects_sequence_gap() -> None:
    first = _fill_view(1, OrderSide.BUY, "1", "100")
    # sequence_no 3을 써서 2를 건너뛴다.
    second = _fill_view(2, OrderSide.BUY, "1", "100").model_copy(update={"sequence_no": 3})

    with pytest.raises(SequenceConflictError):
        _fold([first, second])


def test_apply_one_rejects_swapped_order() -> None:
    """엔트리 순서를 맞바꾸면 sequence_no 위반으로 거부돼야 한다."""
    first = _fill_view(1, OrderSide.BUY, "1", "100")
    second = _fill_view(2, OrderSide.BUY, "1", "100")

    with pytest.raises(SequenceConflictError):
        _fold([second, first])


def test_apply_one_rejects_fill_entry_missing_price() -> None:
    malformed = _bare_view(entry_type=JournalEntryType.FILL, price=None)

    with pytest.raises(ValueError, match="price"):
        _fold([malformed])


def test_apply_one_rejects_unsupported_entry_type() -> None:
    unsupported = _bare_view(
        entry_type=JournalEntryType.CORP_ACTION, qty_delta=Decimal("0"), price=None
    )

    with pytest.raises(UnsupportedEntryTypeError):
        _fold([unsupported])
