"""EM-9 -- domain/algo/vwap.py: volume-profile-weighted slice schedule.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/algo/vwap.py`), #9 EM-9 ("volume profile based"), #10 ("VWAP
volume profile depends on DC-22 data quality; a venue with a shallow
profile is auto-demoted to TWAP, with the reason recorded").

Unlike EM-10's `pov.py` (each slice's cap is evaluated against *that
slice's own* market volume, shortfall carries forward, the final slice is
not exempt), VWAP allocates the *entire* parent quantity across slices in
proportion to `volume_profile`'s shape -- more expected volume in a slice
means a larger planned share of the total, the same "one big trade split
into weighted pieces" family as EM-8's `twap.py` (equal weight is the
special case where every `volume_profile[i]` is identical). The two
therefore share the remainder-on-tail rule: the last slice absorbs
whatever Decimal-division rounding left over, so the sum of children is
always exactly `parent.qty` (verified by `guard.plan_residual`, EM-A1),
and the tail slice is exempt from the per-slice participation check for
the same reason `twap.py`'s is (EM-A1 close-out overrides EM-A2 pacing on
the tail).

Shallow-profile demotion (spec #10): DC-22 aggregation can hand this
module a `volume_profile` that is the right length but mostly empty (a
thinly-traded venue, a gap in tick coverage) -- weighting by a profile
like that would concentrate the whole order into whichever few slices
happened to have any recorded volume, which is not what "VWAP" is
supposed to mean. If fewer than `_MIN_NONZERO_COVERAGE` of the slices
carry a positive volume (or the profile's total volume is zero), this
module discards the profile's *shape* and falls back to an equal-weight
(TWAP-like) allocation, and it also stops trusting individual
`volume_profile[i]` entries for the participation check -- it substitutes
the profile's own per-slice average (`total_volume / slice_count`)
uniformly, since a demoted profile's *distribution* (which slice has
which volume) is exactly what triggered the demotion. `demotion_reason`
on the returned `VwapPlanResult` carries why; this module does not log or
persist it itself (no I/O, pure function per module-table `domain/`
convention) -- the caller decides what "recording" the reason means
(an audit row, a structured log line).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
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
"""Same bound as EM-8's twap.py and EM-10's pov.py -- keeps a too-fine
slice_interval_sec failing closed with an EMS-native error instead of an
unbounded loop."""

_MIN_NONZERO_COVERAGE = Decimal("0.5")
"""A profile where fewer than half the slices carry any recorded volume is
too sparse to weight by -- DC-22 tick-to-candle coverage that thin is a
data-quality problem, not a real intraday volume shape, so it is treated
as shallow (demoted to equal-weight TWAP) rather than honored literally."""


class AlgoConstraintError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- wrong `algo.kind`, a derived slice count
    outside `[1, 500]`, or a `volume_profile` that does not have exactly
    one entry per slice."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class ParentTerminalError(ValueError):
    """EM_PARENT_TERMINAL(409) -- EM-A4: a terminal parent may not spawn
    new children."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.PARENT_TERMINAL


@dataclass(frozen=True)
class VwapPlanResult:
    """Plan output plus the shallow-profile demotion decision (spec #10).

    `demotion_reason` is `None` exactly when `demoted_to_twap` is `False`
    -- callers should not need to parse the string to learn whether a
    demotion happened.
    """

    children: list[ChildOrder]
    demoted_to_twap: bool
    demotion_reason: str | None


def _slice_count(parent: ParentOrder) -> int:
    """Derive `slice_count` from the window and `slice_interval_sec` via
    floor, same rule as EM-8/EM-10 -- floor guarantees the real gap
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


def _allocate_by_weight(total_qty: Decimal, weights: Sequence[Decimal]) -> list[Decimal]:
    """Split `total_qty` across `weights` proportionally, remainder on the
    last entry -- guarantees `sum(result) == total_qty` exactly regardless
    of Decimal-division rounding on the individual shares (mirrors
    twap.py's tail-absorption rule)."""
    weight_sum = sum(weights, Decimal("0"))
    allocated: list[Decimal] = []
    running_total = Decimal("0")
    for weight in weights[:-1]:
        share = (total_qty * weight / weight_sum) if weight_sum > 0 else Decimal("0")
        allocated.append(share)
        running_total += share
    allocated.append(total_qty - running_total)
    return allocated


def plan_vwap_schedule(
    parent: ParentOrder,
    *,
    volume_profile: Sequence[Decimal],
) -> VwapPlanResult:
    """Pure: `ParentOrder` (with `algo.kind == VWAP`) + a market volume
    snapshot -> a verified, deterministic child order plan, demoted to an
    equal-weight (TWAP-like) allocation when the profile is too shallow to
    trust (spec #10).

    `volume_profile[i]` is the expected market volume for slice `i`'s
    interval and must have exactly one entry per derived slice (fail
    closed on any mismatch -- a short profile is never treated as
    "unlimited volume" for the missing tail, matching twap.py/pov.py).
    """
    if parent.algo.kind != AlgoKind.VWAP:
        raise AlgoConstraintError(f"vwap.py only plans AlgoKind.VWAP, got {parent.algo.kind!r}.")
    if parent.status in TERMINAL_ORDER_STATUSES:
        raise ParentTerminalError(
            f"parent {parent.parent_id} is terminal ({parent.status!r}); "
            "no new children may be scheduled."
        )

    slice_count = _slice_count(parent)
    if len(volume_profile) != slice_count:
        raise AlgoConstraintError(
            f"volume_profile must have exactly {slice_count} entries "
            f"(one per VWAP slice), got {len(volume_profile)}."
        )

    scheduled_ats = [
        parent.algo.start + timedelta(seconds=parent.algo.slice_interval_sec * index)
        for index in range(slice_count)
    ]
    for prev, nxt in zip(scheduled_ats, scheduled_ats[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        check_slice_interval(prev, nxt, parent.algo.slice_interval_sec)

    total_volume = sum(volume_profile, Decimal("0"))
    nonzero_count = sum(1 for volume in volume_profile if volume > 0)
    coverage = Decimal(nonzero_count) / Decimal(slice_count)
    demoted = total_volume <= 0 or coverage < _MIN_NONZERO_COVERAGE

    if demoted:
        weights: Sequence[Decimal] = [Decimal(1)] * slice_count
        average_volume = total_volume / slice_count
        market_volumes: Sequence[Decimal] = [average_volume] * slice_count
        demotion_reason: str | None = (
            f"volume_profile nonzero coverage {coverage} is below the "
            f"{_MIN_NONZERO_COVERAGE} minimum (or total volume is zero) -- "
            "demoted to equal-weight TWAP allocation; per-slice participation "
            "now checked against the profile's average volume instead of its "
            "(untrusted) per-slice shape."
        )
    else:
        weights = volume_profile
        market_volumes = volume_profile
        demotion_reason = None

    planned_qtys = _allocate_by_weight(parent.qty, weights)

    last_index = slice_count - 1
    for index, qty in enumerate(planned_qtys):
        if index == last_index:
            continue  # EM-A1 close-out overrides EM-A2 pacing on the tail slice.
        if qty > 0:
            check_participation(qty, market_volumes[index], parent.algo.max_participation_pct)

    plan_residual(parent.qty, Decimal("0"), sum(planned_qtys, Decimal("0")))

    children = [
        ChildOrder(
            child_id=uuid5(parent.parent_id, f"vwap-slice-{index}"),
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
    return VwapPlanResult(
        children=children, demoted_to_twap=demoted, demotion_reason=demotion_reason
    )
