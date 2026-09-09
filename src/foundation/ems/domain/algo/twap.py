"""EM-8 -- domain/algo/twap.py: promote oms/domain/algo_slicer.py to an EMS port.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/algo/twap.py`), #9 EM-8 ("promote the existing oms/domain/
algo_slicer.py to an EMS port (no rewrite)"). DoD: schedule snapshot + determinism (EM-A3).

This leaf does **not** reimplement TWAP slicing --
`src/services/oms/domain/algo_slicer.py`'s `plan_slices()` already does
(deterministic seeded jitter, exact-remainder absorption on the last
slice) and stays the one algorithm. This module is the EMS-side adapter
around it:

1. Translate `ParentOrder`/`AlgoSpec` (EMS contracts, EM-1) into the
   `AlgoRequest` shape `plan_slices()` expects, with jitter pinned to
   zero -- `AlgoSpec` carries no jitter fields (2026-09-06 audit: dropped
   because random jitter under EM-A3 must be reproducible via `seed`
   alone, and `AlgoSpec.seed` already exists for that; this port does not
   add jitter behavior `AlgoSpec` cannot express).
2. Call `plan_slices()` with `volume_profile=None` -- the OMS module's own
   participation softening is optional there (`None` means "no cap
   applied", a legacy default this port does not inherit). All
   participation enforcement here goes through EM-7's `guard.py`
   instead, which is fail-closed by construction (I-10: the guard must be
   demonstrably wired, not merely present).
3. Verify the result with `guard.check_slice_interval` (EM-A3 pacing),
   `guard.check_participation` per non-final slice (EM-A2 -- the final
   slice is exempt because EM-A1 close-out legitimately overrides EM-A2
   pacing on the tail, same rule the OMS test suite already documents),
   and `guard.plan_residual` (EM-A1 -- sum of children never exceeds the
   parent's quantity).
4. Map the verified `SlicePlan` sequence into `ChildOrder` (EMS
   contract). `child_id` is derived with `uuid5` from `(parent_id,
   slice_seq)` rather than `uuid4` -- a random id would make two calls
   with identical inputs produce unequal output, which fails EM-A3
   ("same snapshot + same spec -> same plan").

The `OrderIdempotencyScope` on the synthesized `AlgoRequest` is a
structural placeholder only: `plan_slices()` never reads any scope field
(it only touches `start_at`/`end_at`/`slice_count`/`total_quantity`/
`size_jitter_pct`/`time_jitter_pct`/`max_participation_pct`). The real
submit-time scope is assigned later, per child, when
`application/start_algo.py` (EM-15) calls `submit_order` -- that is the
only place scope is meaningful (spec #3: "child orders pass through
submit_order with no exception").
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from decimal import Decimal
from typing import ClassVar
from uuid import UUID, uuid5

from src.foundation.ems.contracts.v1 import (
    TERMINAL_ORDER_STATUSES,
    AlgoKind,
    AlgoSpec,
    ChildOrder,
    EmsErrorCode,
    ParentOrder,
)
from src.foundation.ems.domain.algo.guard import (
    check_participation,
    check_slice_interval,
    plan_residual,
)
from src.services.oms.contracts.v1_commands import AlgoRequest, OrderIdempotencyScope
from src.services.oms.domain.algo_slicer import plan_slices

_MAX_SLICE_COUNT = 500
"""Matches `AlgoRequest.slice_count`'s `le=500` -- kept here too so a
too-fine `slice_interval_sec` fails closed with an EMS-native error
instead of leaking a raw pydantic `ValidationError` from the OMS type."""

_PLACEHOLDER_PROVIDER = "paper_sim"


class AlgoConstraintError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- wrong `algo.kind`, a derived slice count
    outside `[1, 500]`, or a `volume_profile` that does not have exactly
    one entry per slice."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class ParentTerminalError(ValueError):
    """EM_PARENT_TERMINAL(409) -- EM-A4: a terminal parent may not spawn
    new children."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.PARENT_TERMINAL


def _slice_count(spec: AlgoSpec) -> int:
    """Derive `slice_count` from the window and `slice_interval_sec` via
    floor -- floor guarantees `span / slice_count >= slice_interval_sec`
    always (rounding up the count, i.e. down the interval, would let a
    real gap fall under the spec's minimum and fail
    `guard.check_slice_interval`)."""
    span_sec = (spec.end - spec.start).total_seconds()
    count = max(1, math.floor(span_sec / spec.slice_interval_sec))
    if count > _MAX_SLICE_COUNT:
        raise AlgoConstraintError(
            f"derived slice_count {count} exceeds the {_MAX_SLICE_COUNT} cap -- "
            f"window {span_sec}s is too long for slice_interval_sec "
            f"{spec.slice_interval_sec}s."
        )
    return count


def _to_algo_request(parent: ParentOrder, *, slice_count: int) -> AlgoRequest:
    placeholder_scope = OrderIdempotencyScope(
        tenant_id=UUID(int=0),
        account_ref="",
        provider=_PLACEHOLDER_PROVIDER,
        strategy_id="",
        strategy_version="0",
        execution_id=0,
        intent_seq=0,
        window_start=parent.algo.start,
    )
    return AlgoRequest(
        algo_run_id=parent.parent_id,
        trace_id=parent.parent_id,
        scope=placeholder_scope,
        algo="TWAP",
        symbol=parent.instrument_id,
        side=parent.side,
        total_quantity=parent.qty,
        start_at=parent.algo.start,
        end_at=parent.algo.end,
        slice_count=slice_count,
        max_participation_pct=parent.algo.max_participation_pct,
        size_jitter_pct=Decimal("0"),
        time_jitter_pct=Decimal("0"),
        limit_price=parent.algo.limit_price,
        seed=parent.algo.seed,
    )


def plan_twap_schedule(
    parent: ParentOrder,
    *,
    volume_profile: Sequence[Decimal],
) -> list[ChildOrder]:
    """Pure: `ParentOrder` (with `algo.kind == TWAP`) + a market volume
    snapshot -> a verified, deterministic child order plan.

    `volume_profile[i]` is the expected market volume for slice `i`'s
    interval and must have exactly one entry per derived slice (fail
    closed on any mismatch -- a short profile is never treated as
    "unlimited volume" for the missing tail).
    """
    if parent.algo.kind != AlgoKind.TWAP:
        raise AlgoConstraintError(
            f"twap.py only plans AlgoKind.TWAP, got {parent.algo.kind!r}."
        )
    if parent.status in TERMINAL_ORDER_STATUSES:
        raise ParentTerminalError(
            f"parent {parent.parent_id} is terminal ({parent.status!r}); "
            "no new children may be scheduled."
        )

    slice_count = _slice_count(parent.algo)
    if len(volume_profile) != slice_count:
        raise AlgoConstraintError(
            f"volume_profile must have exactly {slice_count} entries "
            f"(one per TWAP slice), got {len(volume_profile)}."
        )

    algo_request = _to_algo_request(parent, slice_count=slice_count)
    rng = random.Random(parent.algo.seed)  # noqa: S311 -- reproducible jitter, not crypto (EM-A3)
    slice_plans = plan_slices(
        algo_request, now=parent.algo.start, volume_profile=None, rng=rng
    )

    for prev, nxt in zip(slice_plans, slice_plans[1:]):  # noqa: B905 -- pairwise, lengths differ by design
        check_slice_interval(prev.scheduled_at, nxt.scheduled_at, parent.algo.slice_interval_sec)

    last_index = len(slice_plans) - 1
    for index, slice_plan in enumerate(slice_plans):
        if index == last_index:
            continue  # EM-A1 close-out overrides EM-A2 pacing on the tail slice.
        check_participation(
            slice_plan.quantity, volume_profile[index], parent.algo.max_participation_pct
        )

    plan_residual(parent.qty, Decimal("0"), sum((p.quantity for p in slice_plans), Decimal("0")))

    return [
        ChildOrder(
            child_id=uuid5(parent.parent_id, f"twap-slice-{plan.sequence}"),
            parent_id=parent.parent_id,
            slice_seq=plan.sequence,
            instrument_id=parent.instrument_id,
            side=parent.side,
            planned_qty=plan.quantity,
            scheduled_at=plan.scheduled_at,
            limit_price=parent.algo.limit_price,
        )
        for plan in slice_plans
        if plan.quantity > 0
    ]
