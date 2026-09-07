"""RD-19 — `domain/l2_orderbook.py` 단위테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. 스냅샷 적용·증분 적용(upsert/삭제)·음수 수량 거부(negative)·
상위 N호가 정렬을 검증한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from typing import TypedDict

import pytest
from typing_extensions import Unpack

from src.foundation.market_data.domain.l2_orderbook import (
    L2Diff,
    L2Snapshot,
    NegativeQuantityError,
    OrderBookState,
)

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_T1 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)

_DEFAULT_SNAPSHOT = L2Snapshot(
    sequence=100,
    as_of=_T0,
    bids={Decimal("10.0"): Decimal("1"), Decimal("9.5"): Decimal("2")},
    asks={Decimal("10.5"): Decimal("1"), Decimal("11.0"): Decimal("2")},
)


class _SnapshotOverrides(TypedDict, total=False):
    sequence: int
    as_of: datetime
    bids: Mapping[Decimal, Decimal]
    asks: Mapping[Decimal, Decimal]


def _snapshot(**overrides: Unpack[_SnapshotOverrides]) -> L2Snapshot:
    return replace(_DEFAULT_SNAPSHOT, **overrides)


def test_from_snapshot_copies_levels():
    book = OrderBookState.from_snapshot(_snapshot())
    assert book.sequence == 100
    assert book.bids[Decimal("10.0")] == Decimal("1")
    assert book.asks[Decimal("10.5")] == Decimal("1")


def test_apply_diff_upserts_and_removes_levels():
    book = OrderBookState.from_snapshot(_snapshot())
    diff = L2Diff(
        sequence=101,
        as_of=_T1,
        bid_updates=((Decimal("10.0"), Decimal("0")), (Decimal("9.0"), Decimal("3"))),
        ask_updates=((Decimal("10.5"), Decimal("5")),),
    )
    updated = book.apply_diff(diff)

    assert updated.sequence == 101
    assert Decimal("10.0") not in updated.bids  # qty=0 → 삭제
    assert updated.bids[Decimal("9.0")] == Decimal("3")  # 신규 upsert
    assert updated.asks[Decimal("10.5")] == Decimal("5")  # 기존 upsert
    # 원본 상태는 불변으로 남는다.
    assert book.bids[Decimal("10.0")] == Decimal("1")


def test_apply_diff_does_not_mutate_source_state():
    book = OrderBookState.from_snapshot(_snapshot())
    diff = L2Diff(
        sequence=101, as_of=_T1,
        bid_updates=((Decimal("10.0"), Decimal("0")),), ask_updates=(),
    )
    book.apply_diff(diff)
    assert Decimal("10.0") in book.bids


def test_negative_quantity_snapshot_is_rejected():
    """negative — 음수 수량은 조용히 무시하지 않고 fail-closed 예외로 표면화."""
    bad = _snapshot(bids={Decimal("10.0"): Decimal("-1")})
    with pytest.raises(NegativeQuantityError):
        OrderBookState.from_snapshot(bad)


def test_negative_quantity_diff_is_rejected():
    """negative — 증분에서도 음수 수량을 거부한다."""
    book = OrderBookState.from_snapshot(_snapshot())
    diff = L2Diff(
        sequence=101, as_of=_T1, bid_updates=((Decimal("10.0"), Decimal("-5")),), ask_updates=()
    )
    with pytest.raises(NegativeQuantityError):
        book.apply_diff(diff)


def test_top_n_orders_bids_descending_and_asks_ascending():
    book = OrderBookState.from_snapshot(_snapshot())
    top_bids, top_asks = book.top_n(1)
    assert top_bids == [(Decimal("10.0"), Decimal("1"))]
    assert top_asks == [(Decimal("10.5"), Decimal("1"))]
