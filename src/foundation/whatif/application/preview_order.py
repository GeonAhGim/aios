"""UX-10 what-if preview: pass a virtual order through the risk (R-14) and
compliance (CM-8) authorities and report what *would* happen — without ever
submitting an order.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md#UX-10, §3 (a
what-if response carries `would_be_denied_by: [risk|compliance]` plus
evidence reason codes, while the contract guarantees zero order-creation
side effects), §4 UX-A2 (the what-if path performs no writes at all —
proven by static check + adversarial test).

UX-A2 is enforced two ways:
1. This module is 100% synchronous, takes only already-assembled read-only
   snapshots as arguments (`PortfolioAggregate`, `RiskInputs`,
   `MandateRevisionView`) and never accepts a repository/connection/
   session — there is nothing here *to* write through.
   `tests/adversarial/whatif/test_preview_order_write_free.py` statically
   proves the absence of `async def`/`await`/write-verb calls and
   adversarially injects each to prove the checker actually catches them.
2. It calls only pure, zero-I/O entry points on both authorities:
   `check_exposure_limits` (R-14, `core/risk/limits.py`) and
   `preview_pre_trade` (CM-8, `mandates/application/preview_pre_trade.py`
   — a read-only sibling of `evaluate_pre_trade()`, split into its own
   file to respect the architecture guard's line cap), never the impure
   `evaluate_pre_trade()` wrapper, which persists a `policy_decision` row.

This module deliberately never imports `src.foundation.mandates.domain.*`
— cross-aggregate callers are only allowed through `application/`/
`contracts/` (`.importlinter` `boundary:foundation-aggregates`), so the
compliance side of this preview is typed as `MandateRevisionView`
(`mandates/contracts/v1.py`), the same public contract type other bounded
contexts already consume.

Mirrors I-09 (`docs/design/INVARIANTS.md`): a preview is denied unless BOTH
independent authorities — risk and compliance — would allow it. Missing
context for either authority (no `RiskInputs`, no active mandate) is
fail-closed: it counts as a denial from that authority, never a silent pass
(CLAUDE.md §3 "Default posture is fail-closed").
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.core.portfolio.state_input import PortfolioAggregate
from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import RiskInputs
from src.core.risk.limits import ExposureLimit, check_exposure_limits
from src.foundation.mandates.application.preview_pre_trade import preview_pre_trade
from src.foundation.mandates.contracts.v1 import (
    ComplianceVerdict,
    MandateRevisionState,
    MandateRevisionView,
)
from src.foundation.whatif.domain.impact import ProposedTrade, TradeImpact, delta_impact

RISK_INPUTS_UNAVAILABLE = "RISK_INPUTS_UNAVAILABLE"
COMPLIANCE_MANDATE_MISSING = "COMPLIANCE_MANDATE_MISSING"
COMPLIANCE_BUNDLE_INACTIVE = "COMPLIANCE_BUNDLE_INACTIVE"


@dataclass(frozen=True)
class PreviewResult:
    """§3 contract: `would_be_denied_by` names which authority (or both)
    would deny this order, `reason_codes` carries the evidence per source.
    `would_be_denied_by` never contains a source whose key is missing from
    `reason_codes` and vice versa — the two always agree 1:1."""

    impact: TradeImpact
    would_be_denied_by: tuple[str, ...]  # subset of ("risk", "compliance")
    reason_codes: Mapping[str, tuple[str, ...]]
    risk_outcome: RiskOutcome | None  # None only when risk_inputs was unavailable
    compliance_verdict: ComplianceVerdict | None  # None only when mandate was unavailable


def preview_order(
    *,
    before: PortfolioAggregate,
    trade: ProposedTrade,
    risk_inputs: RiskInputs | None,
    risk_limits: tuple[ExposureLimit, ...] = (),
    mandate: MandateRevisionView | None,
    now: datetime,
) -> PreviewResult:
    """Compute portfolio impact (UX-9) and run it past both authorities
    read-only. Raises `ValueError` (propagated from `delta_impact`) for a
    structurally invalid trade (zero/negative notional, unknown side) —
    same as UX-9, this function never guesses a "safe" default for bad
    input.
    """
    impact = delta_impact(before=before, trade=trade)

    denied_by: list[str] = []
    reason_codes: dict[str, tuple[str, ...]] = {}

    risk_outcome = _check_risk(risk_inputs, risk_limits, denied_by, reason_codes)
    compliance_verdict = _check_compliance(
        before, trade, impact, mandate, now, denied_by, reason_codes
    )

    return PreviewResult(
        impact=impact,
        would_be_denied_by=tuple(denied_by),
        reason_codes=reason_codes,
        risk_outcome=risk_outcome,
        compliance_verdict=compliance_verdict,
    )


def _check_risk(
    risk_inputs: RiskInputs | None,
    risk_limits: tuple[ExposureLimit, ...],
    denied_by: list[str],
    reason_codes: dict[str, tuple[str, ...]],
) -> RiskOutcome | None:
    if risk_inputs is None:
        denied_by.append("risk")
        reason_codes["risk"] = (RISK_INPUTS_UNAVAILABLE,)
        return None

    result = check_exposure_limits(risk_inputs, risk_limits)
    if result.outcome != RiskOutcome.ALLOW:
        denied_by.append("risk")
        reason_codes["risk"] = (result.reason_code or result.rule_id,)
    return result.outcome


def _check_compliance(
    before: PortfolioAggregate,
    trade: ProposedTrade,
    impact: TradeImpact,
    mandate: MandateRevisionView | None,
    now: datetime,
    denied_by: list[str],
    reason_codes: dict[str, tuple[str, ...]],
) -> ComplianceVerdict | None:
    if mandate is None:
        denied_by.append("compliance")
        reason_codes["compliance"] = (COMPLIANCE_MANDATE_MISSING,)
        return None
    if mandate.state != MandateRevisionState.ACTIVE:
        denied_by.append("compliance")
        reason_codes["compliance"] = (COMPLIANCE_BUNDLE_INACTIVE,)
        return None

    snapshot = _compliance_snapshot(before, trade, impact)
    preview = preview_pre_trade(mandate, snapshot, now=now)
    if preview.verdict == ComplianceVerdict.DENY:
        denied_by.append("compliance")
        reason_codes["compliance"] = preview.reason_codes
    return preview.verdict


def _compliance_snapshot(
    before: PortfolioAggregate, trade: ProposedTrade, impact: TradeImpact
) -> dict[str, str | float]:
    """Only the two fields `preview_pre_trade` can activate from a
    `PortfolioAggregate`/`TradeImpact` pair — `restricted_list` (`symbol`)
    and `concentration` (`projected_instrument_pct`, the after-trade pct).
    `leverage`/`liquidity`/`position_limit` need data (gross leverage, ADV,
    absolute position notional) this leaf's inputs don't carry — same known
    gap `evaluate_pre_trade.assemble_rule_bundle` already documents;
    omitting the key here leaves that rule inactive rather than fabricating
    a value that could false-negative a real breach.
    """
    old_pct = before.per_symbol_pct.get(trade.symbol, Decimal("0"))
    new_pct = old_pct + impact.per_symbol_pct_delta.get(trade.symbol, Decimal("0"))
    return {"symbol": trade.symbol, "projected_instrument_pct": float(new_pct)}
