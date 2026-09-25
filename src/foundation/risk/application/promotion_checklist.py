"""U-15 PAPER->LIVE promotion checklist application layer.

Connects the pure decision in `domain/promotion.py` to operational state
I/O (file-backed PAPER history). Whether ADR-08-29-E condition 2 is met is
a real-account operational fact the code cannot verify on its own
(unverified), so it is decided solely by the
`PERSONAL_ADR_0829E_CONDITION2_MET` environment variable (default false,
fail-closed).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, datetime, timezone

from src.foundation.risk.domain.promotion import (
    PromotionBlocker,
    PromotionChecklistInput,
    evaluate_promotion_checklist,
)
from src.foundation.risk.ports.state import PersonalOperationStatePort

_CONDITION2_ENV = "PERSONAL_ADR_0829E_CONDITION2_MET"


def _condition2_met() -> bool:
    return os.environ.get(_CONDITION2_ENV, "").strip().lower() in {"1", "true", "yes"}


def _today() -> date:
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class PersonalPromotionChecklist:
    eligible: bool
    blockers: tuple[PromotionBlocker, ...]
    paper_days_elapsed: int
    paper_violation_count: int


class PromotionDeniedError(Exception):
    """LIVE promotion request denied because the checklist is unmet —
    fail-closed."""

    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        self.details = {"blockers": list(blockers)}
        super().__init__(f"promotion denied: {', '.join(blockers)}")


async def build_promotion_checklist(
    *, bundle_active: bool, state: PersonalOperationStatePort
) -> PersonalPromotionChecklist:
    today = _today()
    started = await state.mark_paper_started_if_unset(today=today)
    paper_days_elapsed = (today - started).days
    violation_count = await state.violation_count_since(started)

    result = evaluate_promotion_checklist(
        PromotionChecklistInput(
            adr_0829e_condition2_met=_condition2_met(),
            bundle_active=bundle_active,
            paper_days_elapsed=paper_days_elapsed,
            paper_violation_count=violation_count,
        )
    )
    return PersonalPromotionChecklist(
        eligible=result.eligible,
        blockers=result.blockers,
        paper_days_elapsed=paper_days_elapsed,
        paper_violation_count=violation_count,
    )


async def request_promotion(
    *, bundle_active: bool, state: PersonalOperationStatePort
) -> PersonalPromotionChecklist:
    checklist = await build_promotion_checklist(bundle_active=bundle_active, state=state)
    if not checklist.eligible:
        raise PromotionDeniedError(tuple(b.value for b in checklist.blockers))
    return checklist
