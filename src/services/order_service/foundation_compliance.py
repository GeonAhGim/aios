"""CM-8 — merges `mandates.application.evaluate_pre_trade`'s judgment into a
result `foundation_gate.py` can fold straight into its `GateDecision`. Split
out from `foundation_gate.py` to keep that file under the 300-line cap (P6);
this is the only file in `order_service` that imports `evaluate_pre_trade`
(mirrors `foundation_gate.py`'s own "foundation imports live in one place"
rule for `gate.py`/`submit.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid5

from src.foundation.mandates.application.evaluate_pre_trade import (
    ComplianceBundleInactiveError,
    ComplianceMandateMissingError,
    evaluate_pre_trade,
)
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from src.foundation.mandates.ports.repository import MandateRepository
from src.services.order_service.gate import OrderContext

# Fixed namespace for the "no mandate configured, passthrough" marker id —
# never written to `policy_decision` (no revision to FK a WORM row to), but
# still a stable, non-None id so `submit_order`'s "both ids present" check
# (L4_compliance_and_regulatory_v1.0.md §3) has something real to see even
# when `require_compliance_mandate=False` lets the order through unchecked.
_NO_MANDATE_NAMESPACE = UUID("2b9e6b0e-3a3a-4d7b-9d0e-6a6c6f6b6d8f")


@dataclass(frozen=True)
class ComplianceGateResult:
    allowed: bool
    reason_codes: tuple[str, ...]
    compliance_decision_id: UUID


def _compliance_snapshot(context: OrderContext) -> dict[str, object]:
    """Only fields a real per-order call site actually has today. `context.
    symbol` is `None` for execution-*start* gate calls (`ExecutionService.
    pre_start_gate` — no specific order exists yet); this leaf's rule-bundle
    checks are order-level (§2.2 "order submission path"), so an absent symbol simply
    yields an empty bundle (still a real, persisted ALLOW decision) rather
    than a spurious deny."""
    snapshot: dict[str, object] = {}
    if context.symbol is not None:
        snapshot["symbol"] = context.symbol
    return snapshot


def _no_mandate_marker(tenant_id: UUID) -> UUID:
    return uuid5(_NO_MANDATE_NAMESPACE, str(tenant_id))


async def evaluate_compliance_gate(
    mandate_repo: MandateRepository,
    context: OrderContext,
    *,
    require_compliance_mandate: bool,
    now: datetime,
) -> ComplianceGateResult:
    """CM-A5 — always evaluated, independent of the risk/numeric-mandate
    branch this is called alongside (§0 authority principle). `allowed=True` always
    carries a real, non-None `compliance_decision_id` (`submit_order`'s
    fail-closed contract): a genuine WORM `policy_decision` row when a rule
    bundle actually ran, or a deterministic marker when no mandate is
    configured and `require_compliance_mandate=False` lets that pass — mirrors
    the numeric-policy check's own sibling `require_mandate` flag when that one
    is off. H-1b(task-3369) flipped `require_mandate` to `True` in all three
    production assemblies; this CM-8 flag is an independent axis and still
    defaults to `False` (H-2/CM-11 is its own separate leaf).
    """
    try:
        result = await evaluate_pre_trade(
            mandate_repo,
            tenant_id=context.user_id,
            portfolio_id=None,
            snapshot=_compliance_snapshot(context),
            now=now,
        )
    except ComplianceMandateMissingError:
        marker = _no_mandate_marker(context.user_id)
        if require_compliance_mandate:
            return ComplianceGateResult(False, ("CM_MANDATE_MISSING",), marker)
        return ComplianceGateResult(True, (), marker)
    except ComplianceBundleInactiveError:
        # §6 "inactive bundle -> 409 fail-closed" — always denies, never gated by
        # `require_compliance_mandate` (that flag only covers "mandate is
        # absent", not "mandate exists but is broken/paused").
        return ComplianceGateResult(
            False, ("CM_BUNDLE_INACTIVE",), _no_mandate_marker(context.user_id)
        )

    if result.verdict == ComplianceVerdict.DENY:
        return ComplianceGateResult(False, result.reason_codes, result.compliance_decision_id)
    return ComplianceGateResult(True, (), result.compliance_decision_id)
