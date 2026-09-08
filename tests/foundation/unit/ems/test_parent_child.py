"""EM-2 domain/parent_child.py -- aggregation, propagation, rejection rules."""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.trading import OrderStatus
from src.foundation.ems.contracts.v1 import EmsErrorCode
from src.foundation.ems.domain.parent_child import (
    AlgoConstraintError,
    ChildFillState,
    ParentTerminalError,
    aggregate_parent_state,
    assert_parent_accepts_new_child,
    assert_slice_within_parent_qty,
    children_pending_cancellation,
)

_PARENT_QTY = Decimal("100")


# -- EM-A1: committed child qty must never exceed parent qty --------------


def test_slice_exactly_filling_remaining_capacity_passes() -> None:
    """60 already committed + exactly 40 new == 100 parent qty -- boundary passes."""
    assert_slice_within_parent_qty(_PARENT_QTY, Decimal("60"), Decimal("40"))


def test_slice_one_unit_over_remaining_capacity_is_rejected() -> None:
    """60 already committed + 41 new == 101 > 100 parent qty -- one unit over rejects."""
    with pytest.raises(AlgoConstraintError) as exc_info:
        assert_slice_within_parent_qty(_PARENT_QTY, Decimal("60"), Decimal("41"))
    assert exc_info.value.code == EmsErrorCode.ALGO_CONSTRAINT


# -- EM-A4: terminal parent rejects new children ---------------------------


@pytest.mark.parametrize(
    "terminal_status",
    [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED],
)
def test_terminal_parent_rejects_new_child(terminal_status: OrderStatus) -> None:
    with pytest.raises(ParentTerminalError) as exc_info:
        assert_parent_accepts_new_child(terminal_status)
    assert exc_info.value.code == EmsErrorCode.PARENT_TERMINAL


@pytest.mark.parametrize(
    "open_status",
    [OrderStatus.SUBMITTED, OrderStatus.ACKNOWLEDGED, OrderStatus.PARTIALLY_FILLED],
)
def test_non_terminal_parent_accepts_new_child(open_status: OrderStatus) -> None:
    assert_parent_accepts_new_child(open_status)  # must not raise


# -- aggregation: parent filled_qty/status derived from child rollup ------


def test_aggregate_partial_fills_sum_to_partially_filled() -> None:
    children = [
        ChildFillState(uuid4(), Decimal("30"), OrderStatus.PARTIALLY_FILLED),
        ChildFillState(uuid4(), Decimal("20"), OrderStatus.PARTIALLY_FILLED),
        ChildFillState(uuid4(), Decimal("0"), OrderStatus.ACKNOWLEDGED),
    ]
    filled_qty, status = aggregate_parent_state(_PARENT_QTY, OrderStatus.SUBMITTED, children)
    assert filled_qty == Decimal("50")
    assert status == OrderStatus.PARTIALLY_FILLED


def test_aggregate_full_fill_is_filled() -> None:
    children = [
        ChildFillState(uuid4(), Decimal("60"), OrderStatus.FILLED),
        ChildFillState(uuid4(), Decimal("40"), OrderStatus.FILLED),
    ]
    filled_qty, status = aggregate_parent_state(_PARENT_QTY, OrderStatus.PARTIALLY_FILLED, children)
    assert filled_qty == _PARENT_QTY
    assert status == OrderStatus.FILLED


def test_aggregate_all_children_cancelled_with_zero_fill_is_cancelled() -> None:
    children = [
        ChildFillState(uuid4(), Decimal("0"), OrderStatus.CANCELLED),
        ChildFillState(uuid4(), Decimal("0"), OrderStatus.REJECTED),
    ]
    filled_qty, status = aggregate_parent_state(_PARENT_QTY, OrderStatus.SUBMITTED, children)
    assert filled_qty == Decimal("0")
    assert status == OrderStatus.CANCELLED


def test_aggregate_open_children_with_zero_fill_keeps_current_status() -> None:
    """Still-open children with no fills yet -- not enough to derive a new state."""
    children = [ChildFillState(uuid4(), Decimal("0"), OrderStatus.ACKNOWLEDGED)]
    filled_qty, status = aggregate_parent_state(_PARENT_QTY, OrderStatus.SUBMITTED, children)
    assert filled_qty == Decimal("0")
    assert status == OrderStatus.SUBMITTED


# -- cancel propagation: only open children are targeted -------------------


def test_cancellation_targets_only_open_children() -> None:
    open_child_id = uuid4()
    partially_filled_child_id = uuid4()
    filled_child_id = uuid4()
    cancelled_child_id = uuid4()
    children = [
        ChildFillState(open_child_id, Decimal("0"), OrderStatus.ACKNOWLEDGED),
        ChildFillState(partially_filled_child_id, Decimal("10"), OrderStatus.PARTIALLY_FILLED),
        ChildFillState(filled_child_id, Decimal("100"), OrderStatus.FILLED),
        ChildFillState(cancelled_child_id, Decimal("0"), OrderStatus.CANCELLED),
    ]
    pending = children_pending_cancellation(children)
    assert set(pending) == {open_child_id, partially_filled_child_id}
