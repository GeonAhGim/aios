"""EM-15b -- application/get_algo_progress.py: read-only progress view of a
running algo, closing the gap task-2672's audit found in EM-15 (task-2670):
`start_algo`/`tick_algo`/`cancel_algo` existed with no way to read back
"how far along is this parent's algo run" (submitted slices, remaining
qty, status).

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #9 EM-15 residual
scope (progress query).

No durable `algo_runs` table exists (same MVP-scope note as
`start_algo.py`), so the only source of truth for a running algo is the
in-memory `AlgoRunPlan` `AlgoScheduler` (`tick_algo.py`) holds while a run
is registered -- the caller (the API router, this same leaf) looks the
plan up by `parent_id` and hands it to `get_algo_progress`. This function
itself stays pure (no I/O, matches EM-8~11/start_algo/tick_algo's own
convention): it only derives a snapshot from the plan it is given.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal
from uuid import UUID

from src.foundation.ems.application.start_algo import AlgoRunPlan

AlgoProgressStatus = Literal["running", "cancelled", "complete"]


class AlgoRunNotFoundError(LookupError):
    """No `AlgoRunPlan` is registered for the requested `parent_id` -- either
    the algo never started, already finished and was unregistered
    (`AlgoScheduler.tick_once` drops a completed/cancelled run), or the
    process that ran it restarted (in-memory only, see module docstring).
    The router maps this 1:1 to HTTP 404 -- same "raise a defined error,
    let the router translate it" convention as `ExplainDecisionNotFoundError`
    (`src/api/routers/foundation/compliance.py`).
    """


@dataclass(frozen=True)
class AlgoProgress:
    parent_id: UUID
    status: AlgoProgressStatus
    total_slices: int
    submitted_slices: int
    pending_slices: int
    remaining_qty: Decimal
    demoted_to_twap: bool
    demotion_reason: str | None


def get_algo_progress(plan: AlgoRunPlan | None, *, parent_id: UUID) -> AlgoProgress:
    """Derive `AlgoProgress` from `plan` (the `AlgoScheduler.active_plan(parent_id)`
    lookup result). Raises `AlgoRunNotFoundError` if `plan` is `None`.

    `pending_children()` already filters by `slice_seq not in submitted_slice_seqs`
    (`start_algo.py`), so a `submitted_slice_seqs` entry that does not match any
    known child (a corrupted/stale registration) is silently excluded from both
    counts rather than crashing this read path -- fail-closed on the write side
    (`tick_algo.py`) already guards what gets marked submitted; this function
    only reports what the plan currently says.
    """
    if plan is None:
        raise AlgoRunNotFoundError(f"no active algo run registered for parent_id={parent_id}")

    pending = plan.pending_children()
    total = len(plan.children)
    submitted = total - len(pending)
    remaining_qty = sum((c.planned_qty for c in pending), Decimal("0"))

    if plan.cancelled:
        status: AlgoProgressStatus = "cancelled"
    elif not pending:
        status = "complete"
    else:
        status = "running"

    return AlgoProgress(
        parent_id=parent_id,
        status=status,
        total_slices=total,
        submitted_slices=submitted,
        pending_slices=len(pending),
        remaining_qty=remaining_qty,
        demoted_to_twap=plan.demoted_to_twap,
        demotion_reason=plan.demotion_reason,
    )
