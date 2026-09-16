"""EM-15a -- application/start_algo.py: begin an algorithm run.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`application/{start_algo,tick_algo,cancel_algo}.py` + scheduler wiring +
integration), #9 EM-15 (depends: EM-8~11, EM-6).

`start_algo` selects EM-8~11's `domain/algo/{twap,vwap,pov,is_shortfall}.py`
by `ParentOrder.algo.kind`, produces the full deterministic slice schedule
(EM-A3) once, up front, from the market snapshot the caller supplies, and
hands the resulting `AlgoRunPlan` back for the scheduler
(`tick_algo.AlgoScheduler`, same leaf) to drive slice-by-slice. It never
calls `submit_order` itself -- schedule *planning* and slice *submission*
are deliberately separate steps (module table: "child submission goes
through the existing submit_order" is tick_algo.py's job, not this one's).

No `parent_orders`/`algo_runs` table exists (2026-09-06/EM-3 audit) to
persist `AlgoSpec` across a process restart, so the returned `AlgoRunPlan`
is this leaf's only record of "what a running algo is planning to do" --
the caller (whatever registers the plan with `tick_algo.AlgoScheduler`) is
responsible for keeping it in memory for the run's lifetime. This is an
explicit MVP scope decision, not a silently-faked guarantee: durable
algo-run storage across a process restart is future work (spec #10 already
marks PAPER-stage algo/TCA effects as relative-comparison only).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import ClassVar

from src.foundation.ems.contracts.v1 import AlgoKind, ChildOrder, EmsErrorCode, ParentOrder
from src.foundation.ems.domain.algo.is_shortfall import plan_is_schedule
from src.foundation.ems.domain.algo.pov import plan_pov_schedule
from src.foundation.ems.domain.algo.twap import plan_twap_schedule
from src.foundation.ems.domain.algo.vwap import plan_vwap_schedule


class UnknownAlgoKindError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `AlgoKind.ICEBERG` (or any future kind) has
    no EM-8~11 planner wired here. Spec #1 requires 4 algo kinds at minimum
    (TWAP/VWAP/POV/IS); ICEBERG is absorbed into the `AlgoKind` enum by EM-1
    but has no execution planner yet -- fail closed instead of silently
    treating an unrecognized kind as e.g. TWAP.
    """

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT


@dataclass
class AlgoRunPlan:
    """The one artifact `start_algo` produces and `tick_algo`/`cancel_algo`
    (same leaf) consume -- the frozen slice schedule plus the mutable
    "already submitted" bookkeeping the scheduler advances tick by tick.

    `demoted_to_twap`/`demotion_reason` are populated only for VWAP runs
    (spec #10 shallow-profile demotion, EM-9); `False`/`None` otherwise.
    """

    parent: ParentOrder
    children: list[ChildOrder]
    submitted_slice_seqs: set[int] = field(default_factory=set)
    cancelled: bool = False
    demoted_to_twap: bool = False
    demotion_reason: str | None = None

    def pending_children(self) -> list[ChildOrder]:
        """Children not yet marked submitted, in schedule order."""
        return [c for c in self.children if c.slice_seq not in self.submitted_slice_seqs]

    def is_complete(self) -> bool:
        return not self.pending_children()


_PLANNERS = {
    AlgoKind.TWAP: plan_twap_schedule,
    AlgoKind.POV: plan_pov_schedule,
    AlgoKind.IS: plan_is_schedule,
}


def start_algo(parent: ParentOrder, *, volume_profile: Sequence[Decimal]) -> AlgoRunPlan:
    """Pure (no I/O, matches EM-8~11): produce the initial `AlgoRunPlan` for
    `parent` by dispatching on `parent.algo.kind`.

    Raises whatever `ParentTerminalError`/`AlgoConstraintError` the chosen
    EM-8~11 planner raises (EM-A1/EM-A2/EM-A3/EM-A4 are all already
    enforced there) -- this function adds exactly one more failure mode,
    `UnknownAlgoKindError`, for kinds no planner exists for yet.
    """
    if parent.algo.kind == AlgoKind.VWAP:
        result = plan_vwap_schedule(parent, volume_profile=volume_profile)
        return AlgoRunPlan(
            parent=parent,
            children=result.children,
            demoted_to_twap=result.demoted_to_twap,
            demotion_reason=result.demotion_reason,
        )

    planner = _PLANNERS.get(parent.algo.kind)
    if planner is None:
        raise UnknownAlgoKindError(
            f"start_algo has no EM-8~11 planner wired for kind={parent.algo.kind!r}."
        )
    children = planner(parent, volume_profile=volume_profile)
    return AlgoRunPlan(parent=parent, children=children)
