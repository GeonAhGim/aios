"""U-15 PAPER->LIVE promotion checklist — pure rules.

Spec: task-2749 — "ADR-08-29-E amended condition + this bundle active + 7
violation-free PAPER days". Of the three exit conditions in
ADR-2026-08-29-E §15.6-D, only condition 2 ("4.9 Human Approval security
(MFA + dual-approval) actually applied to a real account") still blocks
LIVE (the other two are already met/over-satisfied, see that ADR). This
module does not itself verify that condition 2 is "actually" met — that is
a real-account operational fact the code cannot know on its own
(unverified); the caller passes in the operator's attestation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MIN_PAPER_DAYS = 7


class PromotionBlocker(str, Enum):
    ADR_0829E_CONDITION2_UNMET = "ADR_0829E_CONDITION2_UNMET"
    BUNDLE_NOT_ACTIVE = "BUNDLE_NOT_ACTIVE"
    INSUFFICIENT_PAPER_HISTORY = "INSUFFICIENT_PAPER_HISTORY"
    PAPER_VIOLATIONS_PRESENT = "PAPER_VIOLATIONS_PRESENT"


@dataclass(frozen=True)
class PromotionChecklistInput:
    adr_0829e_condition2_met: bool
    bundle_active: bool
    paper_days_elapsed: int
    paper_violation_count: int


@dataclass(frozen=True)
class PromotionChecklistResult:
    eligible: bool
    blockers: tuple[PromotionBlocker, ...]


def evaluate_promotion_checklist(
    checklist_input: PromotionChecklistInput,
) -> PromotionChecklistResult:
    """Fail-closed: each condition is checked independently, and if even
    one is unmet, eligible is False — there is no partial-credit
    compromise that allows promotion."""
    blockers: list[PromotionBlocker] = []
    if not checklist_input.adr_0829e_condition2_met:
        blockers.append(PromotionBlocker.ADR_0829E_CONDITION2_UNMET)
    if not checklist_input.bundle_active:
        blockers.append(PromotionBlocker.BUNDLE_NOT_ACTIVE)
    if checklist_input.paper_days_elapsed < MIN_PAPER_DAYS:
        blockers.append(PromotionBlocker.INSUFFICIENT_PAPER_HISTORY)
    if checklist_input.paper_violation_count > 0:
        blockers.append(PromotionBlocker.PAPER_VIOLATIONS_PRESENT)
    return PromotionChecklistResult(eligible=not blockers, blockers=tuple(blockers))
