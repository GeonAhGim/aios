"""EM-2 -- parent/child order aggregation and propagation rules (pure).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table, #4
(EM-A1, EM-A4), #9 EM-2.

This is a reinforcement, not a new subsystem (2026-09-06 audit, decision
field): `orders.parent_order_id`/`algo_run_id` and
`src/services/oms/domain/algo_slicer.py` already carry identity and slice
planning. This module only answers "may this slice be created" (EM-A1),
"may this parent accept a new child" (EM-A4), "what is the parent's
aggregate state" (rollup of child fills), and "which children need a
cancel request propagated" -- it never builds a slice plan and never
submits an order. Child submission always goes through `submit_order`
(no adapter is called from here).

Status values are the shared `OrderStatus` (`src/data/models/trading.py`,
also reused verbatim by `src/foundation/ems/contracts/v1.py`) -- this
module does not define a parallel status axis.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from src.data.models.trading import OrderStatus
from src.foundation.ems.contracts.v1 import TERMINAL_ORDER_STATUSES, EmsErrorCode

__all__ = [
    "TERMINAL_ORDER_STATUSES",
    "AlgoConstraintError",
    "ParentTerminalError",
    "ChildFillState",
    "assert_parent_accepts_new_child",
    "assert_slice_within_parent_qty",
    "assert_can_create_child",
    "aggregate_parent_state",
    "children_pending_cancellation",
    "validate_aggregate_fills",
    "compute_child_state",
]


class AlgoConstraintError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- a slice would push committed child qty past parent qty."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class ParentTerminalError(ValueError):
    """EM_PARENT_TERMINAL(409) -- new child requested against a terminal parent."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.PARENT_TERMINAL


@dataclass(frozen=True)
class ChildFillState:
    """One child order's contribution to its parent's rollup.

    `status` is the child's own `OrderStatus` -- it drives both the
    cancellation-propagation list and the "all children terminal with zero
    fill" branch of the aggregate rule below.
    """

    child_id: UUID
    filled_qty: Decimal
    status: OrderStatus


def assert_parent_accepts_new_child(parent_status: OrderStatus) -> None:
    """EM-A4 -- reject a new child request against a terminal parent.

    `UNKNOWN` is intentionally not terminal (contracts/v1.py TERMINAL_ORDER_STATUSES
    docstring, 8.3 principle) -- it does not raise here.
    """
    if parent_status in TERMINAL_ORDER_STATUSES:
        raise ParentTerminalError(
            f"parent order is terminal ({parent_status.value}) -- no new child allowed"
        )


def assert_slice_within_parent_qty(
    parent_qty: Decimal,
    committed_child_qty: Decimal,
    new_slice_qty: Decimal,
) -> None:
    """EM-A1 -- sum of child quantities must never exceed the parent quantity.

    Equality is allowed (the boundary case): `committed_child_qty +
    new_slice_qty == parent_qty` passes, one unit over rejects.
    """
    total = committed_child_qty + new_slice_qty
    if total > parent_qty:
        raise AlgoConstraintError(
            f"slice qty {new_slice_qty} would bring committed child qty to {total}, "
            f"exceeding parent qty {parent_qty}"
        )


def assert_can_create_child(
    parent_status: OrderStatus,
    parent_qty: Decimal,
    committed_child_qty: Decimal,
    new_slice_qty: Decimal,
) -> None:
    """Convenience combinator: EM-A4 first, then EM-A1 (same precedence a caller needs)."""
    assert_parent_accepts_new_child(parent_status)
    assert_slice_within_parent_qty(parent_qty, committed_child_qty, new_slice_qty)


def _assert_no_negative_fills(children: list[ChildFillState]) -> None:
    """Fail-closed guard -- a child's `filled_qty` can never be negative.

    A negative per-child fill is physically meaningless (fills only ever
    accumulate) and, if let through, could offset a genuine overshoot in
    another child and mask an EM-A1 violation in the aggregate sum.
    """
    for child in children:
        if child.filled_qty < 0:
            raise AlgoConstraintError(
                f"child {child.child_id} has negative filled_qty {child.filled_qty} "
                "-- fills can never be negative"
            )


def aggregate_parent_state(
    parent_qty: Decimal,
    current_status: OrderStatus,
    children: list[ChildFillState],
) -> tuple[Decimal, OrderStatus]:
    """Roll up child fills into the parent's `(filled_qty, status)`.

    Rules (spec #2 "parent-child": parent state is derived from child
    rollup):
    - `filled_qty` is the sum of every child's `filled_qty`.
    - `filled_qty >= parent_qty` (and > 0) -> FILLED.
    - `0 < filled_qty < parent_qty` -> PARTIALLY_FILLED.
    - `filled_qty == 0` and every child is terminal -> CANCELLED (all
      children were cancelled/rejected/expired/failed with no fill).
    - Otherwise (no children yet, or open children with zero fill) ->
      `current_status` is returned unchanged -- this function only
      produces an opinion once there is something to aggregate.

    Raises AlgoConstraintError if any child has a negative `filled_qty`.
    """
    _assert_no_negative_fills(children)
    filled_qty = sum((child.filled_qty for child in children), start=Decimal("0"))

    if filled_qty > 0 and filled_qty >= parent_qty:
        return filled_qty, OrderStatus.FILLED
    if filled_qty > 0:
        return filled_qty, OrderStatus.PARTIALLY_FILLED
    if children and all(child.status in TERMINAL_ORDER_STATUSES for child in children):
        return filled_qty, OrderStatus.CANCELLED
    return filled_qty, current_status


def children_pending_cancellation(children: list[ChildFillState]) -> list[UUID]:
    """EM-A4 propagation -- open (non-terminal) child ids a parent cancel must reach.

    Terminal children (already FILLED/CANCELLED/REJECTED/EXPIRED/FAILED) are
    excluded -- there is nothing left to cancel on them.
    """
    return [child.child_id for child in children if child.status not in TERMINAL_ORDER_STATUSES]


def validate_aggregate_fills(
    parent_id: UUID,
    children: list[ChildFillState],
    parent_qty: Decimal,
) -> None:
    """EM-A1 guard -- reject when aggregate child fills exceed parent quantity.

    This is a fail-closed invariant check: even if the audit layer drops
    its log entry, the domain layer must still raise AlgoConstraintError
    because it owns the invariant independently.

    Raises AlgoConstraintError when ``sum(child.filled_qty for child in children)``
    is strictly greater than ``parent_qty``.  Message matches
    ``aggregate.*exceeds`` for test assertion.

    Also raises AlgoConstraintError if any individual child has a negative
    `filled_qty` -- otherwise a negative value could offset a genuine
    overshoot elsewhere in the sum and mask an EM-A1 violation.
    """
    _assert_no_negative_fills(children)
    total = sum((child.filled_qty for child in children), start=Decimal("0"))
    if total > parent_qty:
        raise AlgoConstraintError(
            f"aggregate fill {total} exceeds parent qty {parent_qty} (parent={parent_id})"
        )


def compute_child_state(
    parent_id: UUID,
    children: list[ChildFillState],
    parent_qty: Decimal,
) -> None:
    """Thin wrapper around ``validate_aggregate_fills`` for performance testing.

    Delegates to the domain invariant check; returns ``None`` on success.
    """
    validate_aggregate_fills(parent_id, children, parent_qty)
