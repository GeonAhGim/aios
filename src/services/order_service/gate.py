"""Pre-submit risk gate for orders -- defines pure types only.

This module deliberately does not import `src/foundation/*` -- if
`submit.py` knew about foundation directly, it would create unwanted
coupling between the legacy execution path (SCAFFOLD) and the new
Foundation context (PM directive, 2026-09-03 mandate/kill-switch wiring
work). The actual foundation call is implemented by `foundation_gate.py`
(separate from this module -- the side that knows foundation), and the
assembly point (scheduler) injects that implementation via
`submit_order(pre_submit_gate=...)`.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from uuid import UUID

from src.core.observability.metric_names import (
    RISK_DECISION_COUNT_TOTAL,
    RISK_EVALUATION_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort


class GateOutcome(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class OrderContext:
    user_id: UUID
    execution_id: int | None
    exchange: str
    mandate_revision_id: UUID | None
    # R-36 -- if the R-33 fence (F0) was observed at an earlier evaluation
    # point such as PRE_TRADE, it is carried here. The key is
    # `"{SafetyScope.value}:{scope_ref}"` (filled in by foundation_gate.py --
    # this module does not know foundation, so it does not use SafetyScope directly).
    # None (default) skips the freshness comparison — since task-1361,
    # tick.py actually fills in the F0 it observed at PRE_TRADE
    # (observed_fence=phase.fence_snapshot). Since task-1806,
    # mandate_revision_id likewise carries a real value through (the column
    # exists, but there is no UI path yet that fills it in when an
    # execution starts, so it can still be None).
    observed_fence: Mapping[str, int] | None = None
    # task-1717 P0-D -- the order-intent binding key (I10). Used by
    # `fenced_submit` to cross-check the WORM `inputs_snapshot` against the
    # actual order (decision_binding.py). If left None (e.g. a call site with
    # no specific order yet, like `ExecutionService.start()`'s
    # execution-start gate), a decision made with this context cannot pass
    # fenced_submit's binding verification -- only real per-order call sites fill it in.
    symbol: str | None = None
    side: str | None = None
    quantity: Decimal | None = None


@dataclass(frozen=True)
class GateDecision:
    outcome: GateOutcome
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    # R-36 -- the fence snapshot (F0) this decision is based on. Always
    # filled in (foundation_gate reads it alongside every evaluation) --
    # passed through as-is so the next stage (R-37 fenced_submit) can compare
    # it against the value re-fetched right before the actual write and
    # reject it if stale.
    fence_snapshot: Mapping[str, int] = field(default_factory=dict)
    # R-37 -- the `risk_decision.decision_id` that produced this decision
    # (§2.5 `GateDecision += decision_id`). `fenced_submit.submit_with_fence`
    # rejects the order claim outright if this value is None (I1 Master
    # Authority, fail-closed) -- since `foundation_gate.py` does not yet
    # create a `risk_decision` row (before the evaluate_pre_submit
    # delegation, per that file's docstring), this is still filled with None
    # for now. The caller that fills in a real value appears in the
    # R-32/evaluate_pre_submit delegation leaf.
    decision_id: UUID | None = None
    # task-1717 P0-D -- the id of the mandate's own policy decision (the
    # `mandates` bounded context's `PolicyDecisionView.id`, a separate
    # table/schema from risk_gate's WORM `risk_decision`). None if no mandate
    # is connected or none was evaluated -- unlike `decision_id` (risk_gate),
    # this is not subject to fail-closed enforcement (an audit/lookup
    # reference only).
    policy_decision_id: UUID | None = None
    # CM-8 -- the `ComplianceDecision.decision_id` (or its WORM
    # `policy_decision.id`) emitted by
    # `mandates.application.evaluate_pre_trade`. A judgment axis separate
    # from `policy_decision_id` (mandate numeric policy) (§0 authority
    # principle: risk and compliance are two separate authorities).
    # `foundation_gate.py`'s ALLOW return path always fills this in (even
    # with require_compliance_mandate=False -- evaluation itself always
    # happens, and that flag only decides pass/fail for an unconfigured
    # mandate). `submit_order` rejects when outcome is ALLOW but this value
    # is None (L4_compliance_and_regulatory_v1.0.md §3).
    compliance_decision_id: UUID | None = None


PreSubmitGate = Callable[[OrderContext], Awaitable[GateDecision]]


def record_gate_decision(
    metrics: MetricsPort,
    decision: GateDecision,
    *,
    duration_seconds: float,
    engine: str = "core",
) -> None:
    """§7.2 `aios.risk.decision.count_total`/`aios.risk.evaluation.duration_seconds`.

    Because this module is a pure-type module that does not know about
    foundation (per the docstring above), it is instrumented not by the gate
    execution itself but by the caller (`submit.py`) that consumes its
    result.
    """
    reason_code = decision.reason_codes[0] if decision.reason_codes else "none"
    metrics.counter(
        RISK_DECISION_COUNT_TOTAL,
        labels={"engine": engine, "effect": decision.outcome.value, "reason_code": reason_code},
    )
    metrics.observe(RISK_EVALUATION_DURATION_SECONDS, duration_seconds, labels={"engine": engine})
