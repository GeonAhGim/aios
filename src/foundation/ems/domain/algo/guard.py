"""EM-7 -- domain/algo/guard.py: pure algorithm constraint checks.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`domain/algo/guard.py`), #3 contract summary (`AlgoSpec`), #4 (EM-A1~A4),
#9 EM-7.

This module only checks a slice plan or snapshot that some other leaf
(EM-8~10: TWAP/VWAP/POV) already produced -- it does not generate a
schedule and does not touch `src/services/oms/domain/algo_slicer.py` (the
2026-09-06 audit decision reserves schedule generation for EM-8+, and
`AlgoSpec` from `contracts/v1.py` for EM-1; this leaf defines no new
DTOs). Every check here is fail-closed (EM-A2): a missing, `None`, or
out-of-range cap is a rejection, never an "unlimited" interpretation.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import ClassVar

from src.foundation.ems.contracts.v1 import EmsErrorCode


class ParticipationExceededError(ValueError):
    """EM_PARTICIPATION_EXCEEDED(409) -- planned qty exceeds the participation
    cap, or the cap/market volume inputs cannot support a bounded check
    (fail-closed)."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.PARTICIPATION_EXCEEDED


class SliceIntervalError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- the actual gap between two slice start
    times is shorter than `AlgoSpec.slice_interval_sec`, or an input is
    malformed (naive datetime, non-positive interval)."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


class ResidualOverflowError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- EM-A1: filled + scheduled qty would exceed
    the parent order's qty."""

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


def check_participation(
    planned_qty: Decimal,
    market_volume: Decimal,
    max_participation_pct: Decimal | None,
) -> None:
    """EM-A2 participation cap -- reject if planned/market_volume exceeds the cap.

    Fail-closed on every malformed input, none of which are treated as "no
    cap":
    - `max_participation_pct` is `None`, `<= 0`, or `> 100` -- a guard that
      let a missing/invalid cap through would let an algorithm loosen its
      own risk/compliance limit, which is exactly what EM-A2 forbids.
    - `market_volume <= 0` -- there is no market to measure participation
      against; this returns an explicit rejection rather than raising
      `ZeroDivisionError` (which would look like a bug, not a decision).
    - `planned_qty < 0` -- not a valid slice quantity.

    The boundary is inclusive: `planned_qty / market_volume * 100 ==
    max_participation_pct` passes; one unit over rejects.
    """
    if max_participation_pct is None or max_participation_pct <= 0 or max_participation_pct > 100:
        raise ParticipationExceededError(
            f"max_participation_pct must be a value in (0, 100], got "
            f"{max_participation_pct!r} -- a missing or out-of-range cap is never "
            "treated as unlimited."
        )
    if market_volume <= 0:
        raise ParticipationExceededError(
            f"market_volume must be > 0 to evaluate participation, got {market_volume} "
            "-- rejecting closed rather than dividing by zero."
        )
    if planned_qty < 0:
        raise ParticipationExceededError(f"planned_qty must be >= 0, got {planned_qty}.")

    participation_pct = (planned_qty / market_volume) * 100
    if participation_pct > max_participation_pct:
        raise ParticipationExceededError(
            f"planned_qty {planned_qty} is {participation_pct}% of market_volume "
            f"{market_volume}, exceeding the cap of {max_participation_pct}%."
        )


def _require_tz_aware(label: str, value: datetime) -> None:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise SliceIntervalError(f"{label} must be tz-aware, got a naive datetime: {value!r}.")


def check_slice_interval(
    prev_start: datetime,
    next_start: datetime,
    slice_interval_sec: int,
) -> None:
    """EM-A3 slice pacing -- reject if the actual gap is shorter than the
    spec's `slice_interval_sec`.

    Both timestamps must be tz-aware (a naive datetime is ambiguous about
    which wall clock it belongs to and is rejected outright, matching the
    project-wide tz-aware-UTC convention). The boundary is inclusive:
    `actual_gap_sec == slice_interval_sec` passes.
    """
    _require_tz_aware("prev_start", prev_start)
    _require_tz_aware("next_start", next_start)
    if slice_interval_sec <= 0:
        raise SliceIntervalError(f"slice_interval_sec must be > 0, got {slice_interval_sec}.")

    actual_gap_sec = (next_start - prev_start).total_seconds()
    if actual_gap_sec < slice_interval_sec:
        raise SliceIntervalError(
            f"actual gap {actual_gap_sec}s between slices is shorter than the "
            f"required slice_interval_sec {slice_interval_sec}s."
        )


def plan_residual(
    parent_qty: Decimal,
    filled_qty: Decimal,
    scheduled_qty: Decimal,
) -> Decimal:
    """EM-A1 residual close-out -- the unfilled, unscheduled remainder of a
    parent order.

    Returns `parent_qty - filled_qty - scheduled_qty`, which is never
    negative: the two committed quantities are checked against
    `parent_qty` up front, and `ResidualOverflowError` is raised the moment
    their sum would exceed it (EM-A1: child qty sum <= parent qty).
    """
    if filled_qty < 0:
        raise ResidualOverflowError(f"filled_qty must be >= 0, got {filled_qty}.")
    if scheduled_qty < 0:
        raise ResidualOverflowError(f"scheduled_qty must be >= 0, got {scheduled_qty}.")

    committed_qty = filled_qty + scheduled_qty
    if committed_qty > parent_qty:
        raise ResidualOverflowError(
            f"filled_qty {filled_qty} + scheduled_qty {scheduled_qty} = {committed_qty} "
            f"exceeds parent_qty {parent_qty}."
        )
    return parent_qty - committed_qty
