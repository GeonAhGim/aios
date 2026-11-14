"""RD-19 — `domain/l2_orderbook.py` 단위테스트.

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3. 스냅샷 적용·증분 적용(upsert/삭제)·음수 수량 거부(negative)·
상위 N호가 정렬을 검증한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypedDict, Unpack

import pytest

from src.foundation.market_data.domain.l2_orderbook import (
    L2Diff,
    L2Snapshot,
    NegativeQuantityError,
    OrderBookState,
)

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_T1 = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)

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
        sequence=101,
        as_of=_T1,
        bid_updates=((Decimal("10.0"), Decimal("0")),),
        ask_updates=(),
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


def test_negative_quantity_asks_snapshot_is_rejected():
    """negative — asks 측 음수 수량도 스냅샷에서 거부한다."""
    bad = _snapshot(asks={Decimal("10.5"): Decimal("-1")})
    with pytest.raises(NegativeQuantityError):
        OrderBookState.from_snapshot(bad)


def test_negative_quantity_asks_diff_is_rejected():
    """negative — 증분 asks 측에서도 음수 수량을 거부한다."""
    book = OrderBookState.from_snapshot(_snapshot())
    diff = L2Diff(
        sequence=101, as_of=_T1, bid_updates=(), ask_updates=((Decimal("10.5"), Decimal("-3")),)
    )
    with pytest.raises(NegativeQuantityError):
        book.apply_diff(diff)


def test_top_n_orders_bids_descending_and_asks_ascending():
    book = OrderBookState.from_snapshot(_snapshot())
    top_bids, top_asks = book.top_n(1)
    assert top_bids == [(Decimal("10.0"), Decimal("1"))]
    assert top_asks == [(Decimal("10.5"), Decimal("1"))]


def test_top_n_boundary_negative_zero():
    """negative — top_n(n<0) 은 슬라이싱으로 조용히 잘리지 않고 ValueError 로 거부한다.
    경계 — top_n(0) 은 예외 없이 빈 시퀀스를 반환한다."""
    book = OrderBookState.from_snapshot(_snapshot())
    with pytest.raises(ValueError):
        book.top_n(-1)

    top_bids_zero, top_asks_zero = book.top_n(0)
    assert top_bids_zero == []
    assert top_asks_zero == []


def test_top_n_failure_injection_sort_error_propagates(monkeypatch):
    """failure-injection — 정렬 의존성 예외는 삼키지 않고 전파하며 원본 상태는 변하지 않는다."""
    from src.foundation.market_data.domain import l2_orderbook

    book = OrderBookState.from_snapshot(_snapshot())

    def _boom(*_args, **_kwargs):
        raise RuntimeError("injected sort failure")

    monkeypatch.setattr(l2_orderbook, "sorted", _boom, raising=False)
    with pytest.raises(RuntimeError, match="injected sort failure"):
        book.top_n(1)
    monkeypatch.undo()

    assert book.bids[Decimal("10.0")] == Decimal("1")
    assert book.asks[Decimal("10.5")] == Decimal("1")


def test_apply_diff_failure_injection_validator_error_keeps_source(monkeypatch):
    """failure-injection — 검증 예외 시 apply_diff 는 새 상태를 만들지 않고 원본을 유지한다."""
    from src.foundation.market_data.domain import l2_orderbook

    book = OrderBookState.from_snapshot(_snapshot())
    diff = L2Diff(
        sequence=101, as_of=_T1, bid_updates=((Decimal("9.0"), Decimal("3")),), ask_updates=()
    )

    def _boom(_updates):
        raise RuntimeError("injected validator failure")

    monkeypatch.setattr(l2_orderbook, "_validate_diff_side", _boom)
    with pytest.raises(RuntimeError, match="injected validator failure"):
        book.apply_diff(diff)
    monkeypatch.undo()

    assert book.sequence == 100
    assert Decimal("9.0") not in book.bids


def test_top_n_perf_budget(perf_budget):
    """perf — 2 레벨 호가에서 top_n(50) 은 예산 내 완료된다 (perf_budget 경유)."""
    book = OrderBookState.from_snapshot(_snapshot())
    perf_budget.assert_within(lambda: book.top_n(50), budget_ms=5.0)


def test_top_n_returns_min_n_len():
    """top_n(n) 은 n 이 실제 수준 수보다 많을 때 min(n, len) 개만 반환한다."""
    book = OrderBookState.from_snapshot(_snapshot())
    # bids 2 개, asks 2 개
    top_bids, top_asks = book.top_n(10)
    assert len(top_bids) == 2
    assert len(top_asks) == 2
    # 전체 정렬 순서 확인 (bids 내림차순, asks 오름차순)
    bid_prices = [p for p, _ in top_bids]
    ask_prices = [p for p, _ in top_asks]
    assert bid_prices == sorted(bid_prices, reverse=True)
    assert ask_prices == sorted(ask_prices)


def test_empty_snapshot():
    """경계값 — bids/asks 가 모두 빈 스냅샷을 적용해도 정상 상태다."""
    empty = L2Snapshot(sequence=200, as_of=_T0, bids={}, asks={})
    book = OrderBookState.from_snapshot(empty)
    assert book.bids == {}
    assert book.asks == {}
    assert book.sequence == 200
    # top_n 도 빈 리스트 반환
    top_bids, top_asks = book.top_n(5)
    assert top_bids == []
    assert top_asks == []


def test_top_n_price_sort_order_decimals():
    """top_n 은 Decimal 가격으로 정렬해도 올바르게 동작한다 (소수점 정렬 보장)."""
    book = OrderBookState.from_snapshot(
        _snapshot(
            bids={
                Decimal("10.0"): Decimal("1"),
                Decimal("9.5"): Decimal("2"),
                Decimal("9.9"): Decimal("3"),
                Decimal("9.0"): Decimal("4"),
            },
            asks={
                Decimal("10.5"): Decimal("1"),
                Decimal("11.0"): Decimal("2"),
                Decimal("10.1"): Decimal("3"),
                Decimal("12.0"): Decimal("4"),
            },
        )
    )
    top_bids, top_asks = book.top_n(3)
    # bids 내림차순: 10.0, 9.9, 9.5
    assert [p for p, _ in top_bids] == [Decimal("10.0"), Decimal("9.9"), Decimal("9.5")]
    # asks 오름차순: 10.1, 10.5, 11.0
    assert [p for p, _ in top_asks] == [Decimal("10.1"), Decimal("10.5"), Decimal("11.0")]
