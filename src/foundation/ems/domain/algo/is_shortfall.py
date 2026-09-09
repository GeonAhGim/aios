"""EM-11 -- domain/algo/is_shortfall.py: urgency-cost tradeoff schedule.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/algo/is_shortfall.py`), #9 EM-11 ("urgency-cost tradeoff").

Implementation Shortfall trades timing risk (the cost of waiting, which
grows with adverse price drift) against market impact (the cost of
trading fast). This leaf encodes that tradeoff as one linear weight
schedule keyed by `AlgoSpec.urgency` in `[0, 1]`:

    weight[i] = (1 - urgency) + urgency * (slice_count - i)

At `urgency == 0` every slice gets weight `1` -- the schedule collapses
to an equal split, i.e. TWAP's definition of "no timing-risk aversion".
At `urgency == 1` weight is the strictly decreasing arithmetic sequence
`[n, n-1, ..., 1]` -- most quantity is front-loaded into the earliest
slices, accepting a larger near-term participation/impact cost in
exchange for less exposure to further price drift. Intermediate urgency
values are a convex combination of the two flat-vs-front-loaded weight
vectors, so the front-loading is monotone in urgency: a larger urgency
never yields a smaller first-slice share.

Like EM-10's `pov.py`, this module reuses EM-7's `guard.py` for every
participation/interval/residual check and never computes a participation
ratio itself. Unlike POV, IS does not drop quantity a slice cannot place
under `guard.check_participation`'s cap -- it carries that shortfall
forward into the next slice's target (a `carry` accumulator), so scarce
early liquidity delays, but never destroys, shortfall-averse quantity
(EM-A1).

The final slice is never weight-derived: it absorbs whatever quantity
remains once all earlier slices are capped, which is exactly what
`guard.plan_residual` verifies (parent_qty - filled - scheduled == 0 on a
fully scheduled plan) -- consistent with EM-A1 close-out overriding EM-A2
pacing on the tail, the same rule twap.py/pov.py already document. This
module reuses `AlgoConstraintError`/`ParentTerminalError` from EM-8's
`twap.py` rather than declaring a third copy of the same two exceptions.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from uuid import uuid5

from src.foundation.ems.contracts.v1 import (
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    ChildOrder,
    ParentOrder,
)
from src.foundation.ems.domain.algo.guard import (
    check_participation,
    check_slice_interval,
    plan_residual,
)
from src.foundation.ems.domain.algo.twap import AlgoConstraintError, ParentTerminalError

_MAX_SLICE_COUNT = 500
"""Same bound as EM-8's twap.py / EM-10's pov.py."""


def _slice_count(parent: ParentOrder) -> int:
    """Derive `slice_count` from the window and `slice_interval_sec`, same
    floor rule as twap.py/pov.py (floor keeps the real gap >= the spec's
    minimum)."""
    span_sec = (parent.algo.end - parent.algo.start).total_seconds()
    count = max(1, math.floor(span_sec / parent.algo.slice_interval_sec))
    if count > _MAX_SLICE_COUNT:
        raise AlgoConstraintError(
            f"derived slice_count {count} exceeds the {_MAX_SLICE_COUNT} cap -- "
            f"window {span_sec}s is too long for slice_interval_sec "
            f"{parent.algo.slice_interval_sec}s."
        )
    return count


def _urgency_weights(urgency: Decimal, slice_count: int) -> list[Decimal]:
    """`weight[i] = (1 - urgency) + urgency * (slice_count - i)` -- a convex
    combination of the flat TWAP weight (`1`) and the fully front-loaded
    weight (`slice_count - i`). Exact `Decimal` arithmetic (no division),
    and non-increasing in `i` for every urgency in `[0, 1]`."""
    one = Decimal("1")
    return [
        (one - urgency) + urgency * Decimal(slice_count - index) for index in range(slice_count)
    ]


def plan_is_schedule(
    parent: ParentOrder,
    *,
    volume_profile: Sequence[Decimal],
) -> list[ChildOrder]:
    """Pure: `ParentOrder` (with `algo.kind == IS`) + a market volume
    snapshot -> a verified, deterministic child order plan.

    `volume_profile[i]` is the expected market volume for slice `i`'s
    interval and must have exactly one entry per derived slice (fail
    closed on any mismatch, matching twap.py/pov.py).
    """
    if parent.algo.kind != AlgoKind.IS:
        raise AlgoConstraintError(
            f"is_shortfall.py only plans AlgoKind.IS, got {parent.algo.kind!r}."
        )
    if parent.status in TERMINAL_ORDER_STATUSES:
        raise ParentTerminalError(
            f"parent {parent.parent_id} is terminal ({parent.status!r}); "
            "no new children may be scheduled."
        )

    slice_count = _slice_count(parent)
    if len(volume_profile) != slice_count:
        raise AlgoConstraintError(
            f"volume_profile must have exactly {slice_count} entries "
            f"(one per IS slice), got {len(volume_profile)}."
        )

    max_participation_pct = parent.algo.max_participation_pct
    scheduled_ats = [
        parent.algo.start + timedelta(seconds=parent.algo.slice_interval_sec * index)
        for index in range(slice_count)
    ]
    for prev, nxt in zip(scheduled_ats, scheduled_ats[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        check_slice_interval(prev, nxt, parent.algo.slice_interval_sec)

    weights = _urgency_weights(parent.algo.urgency, slice_count)
    total_weight = sum(weights, Decimal("0"))
    raw_qtys = [parent.qty * weight / total_weight for weight in weights]

    last_index = slice_count - 1
    remaining_qty = parent.qty
    carry = Decimal("0")
    planned_qtys: list[Decimal] = []
    for index in range(last_index):
        desired = min(raw_qtys[index] + carry, remaining_qty)
        cap_qty = max(Decimal("0"), volume_profile[index] * max_participation_pct / Decimal(100))
        slice_qty = max(Decimal("0"), min(desired, cap_qty))
        if slice_qty > 0:
            check_participation(slice_qty, volume_profile[index], max_participation_pct)
        carry = desired - slice_qty
        remaining_qty -= slice_qty
        planned_qtys.append(slice_qty)
    planned_qtys.append(remaining_qty)  # EM-A1 close-out: the tail absorbs whatever is left.

    plan_residual(parent.qty, Decimal("0"), sum(planned_qtys, Decimal("0")))

    return [
        ChildOrder(
            child_id=uuid5(parent.parent_id, f"is-slice-{index}"),
            parent_id=parent.parent_id,
            slice_seq=index,
            instrument_id=parent.instrument_id,
            side=parent.side,
            planned_qty=qty,
            scheduled_at=scheduled_at,
            limit_price=parent.algo.limit_price,
        )
        for index, (qty, scheduled_at) in enumerate(zip(planned_qtys, scheduled_ats, strict=True))
        if qty > 0
    ]
