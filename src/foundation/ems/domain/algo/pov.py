"""EM-10 -- domain/algo/pov.py: real-time participation-rate schedule.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/algo/pov.py`), #9 EM-10 ("real-time participation tracking").

Unlike EM-8's `twap.py` (equal time slices, remainder absorbed on the
tail), POV allocates each slice's quantity from *that slice's own market
volume* -- `volume_profile[i] * max_participation_pct / 100` -- and never
exceeds that cap, not even on the final slice: POV legitimately leaves
qty unfilled if the market does not trade enough volume within the
window, and any shortfall carries forward to the next slice rather than
being dumped past the cap on a later one. This module does not call
`src/services/oms/domain/algo_slicer.py` (that planner's remainder
absorption and size/time jitter are TWAP-specific, EM-8's job) -- it only
reuses EM-7's `guard.py` for the participation/interval/residual checks
themselves. This module never divides a planned quantity by a market
volume to compare against the cap directly; that comparison is only ever
evaluated by `guard.check_participation`.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from typing import ClassVar
from uuid import uuid5

from src.foundation.ems.contracts.v1 import (
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    ChildOrder,
    EmsErrorCode,
    ParentOrder,
)
from src.foundation.ems.domain.algo.guard import (
    check_participation,
    check_slice_interval,
    plan_residual,
)

_MAX_SLICE_COUNT = 500
"""Same bound as EM-8's twap.py -- keeps a too-fine slice_interval_sec
failing closed with an EMS-native error instead of an unbounded loop."""


class AlgoConstraintError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- wrong `algo.kind`, a derived slice count
    outside `[1, 500]`, or a `volume_profile` that does not have exactly
    one entry per slice."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class ParentTerminalError(ValueError):
    """EM_PARENT_TERMINAL(409) -- EM-A4: a terminal parent may not spawn
    new children."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.PARENT_TERMINAL


def _slice_count(parent: ParentOrder) -> int:
    """Derive `slice_count` from the window and `slice_interval_sec` via
    floor, same rule as EM-8's twap.py -- floor guarantees the real gap
    between consecutive slices is never shorter than `slice_interval_sec`."""
    span_sec = (parent.algo.end - parent.algo.start).total_seconds()
    count = max(1, math.floor(span_sec / parent.algo.slice_interval_sec))
    if count > _MAX_SLICE_COUNT:
        raise AlgoConstraintError(
            f"derived slice_count {count} exceeds the {_MAX_SLICE_COUNT} cap -- "
            f"window {span_sec}s is too long for slice_interval_sec "
            f"{parent.algo.slice_interval_sec}s."
        )
    return count


def plan_pov_schedule(
    parent: ParentOrder,
    *,
    volume_profile: Sequence[Decimal],
) -> list[ChildOrder]:
    """Pure: `ParentOrder` (with `algo.kind == POV`) + a market volume
    snapshot -> a verified, deterministic child order plan.

    `volume_profile[i]` is the expected market volume for slice `i`'s
    interval and must have exactly one entry per derived slice (fail
    closed on any mismatch -- a short profile is never treated as
    "unlimited volume" for the missing tail, matching twap.py).
    """
    if parent.algo.kind != AlgoKind.POV:
        raise AlgoConstraintError(f"pov.py only plans AlgoKind.POV, got {parent.algo.kind!r}.")
    if parent.status in TERMINAL_ORDER_STATUSES:
        raise ParentTerminalError(
            f"parent {parent.parent_id} is terminal ({parent.status!r}); "
            "no new children may be scheduled."
        )

    slice_count = _slice_count(parent)
    if len(volume_profile) != slice_count:
        raise AlgoConstraintError(
            f"volume_profile must have exactly {slice_count} entries "
            f"(one per POV slice), got {len(volume_profile)}."
        )

    max_participation_pct = parent.algo.max_participation_pct
    scheduled_ats = [
        parent.algo.start + timedelta(seconds=parent.algo.slice_interval_sec * index)
        for index in range(slice_count)
    ]
    for prev, nxt in zip(scheduled_ats, scheduled_ats[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        check_slice_interval(prev, nxt, parent.algo.slice_interval_sec)

    remaining_qty = parent.qty
    planned_qtys: list[Decimal] = []
    for index in range(slice_count):
        cap_qty = max(Decimal("0"), volume_profile[index] * max_participation_pct / Decimal(100))
        slice_qty = min(cap_qty, remaining_qty)
        if slice_qty > 0:
            check_participation(slice_qty, volume_profile[index], max_participation_pct)
        remaining_qty -= slice_qty
        planned_qtys.append(slice_qty)

    plan_residual(parent.qty, Decimal("0"), sum(planned_qtys, Decimal("0")))

    return [
        ChildOrder(
            child_id=uuid5(parent.parent_id, f"pov-slice-{index}"),
            parent_id=parent.parent_id,
            slice_seq=index,
            instrument_id=parent.instrument_id,
            side=parent.side,
            planned_qty=qty,
            scheduled_at=scheduled_at,
            limit_price=parent.algo.limit_price,
        )
        for index, (qty, scheduled_at) in enumerate(
            zip(planned_qtys, scheduled_ats, strict=True)
        )
        if qty > 0
    ]
