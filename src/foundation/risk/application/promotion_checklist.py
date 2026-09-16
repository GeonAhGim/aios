"""U-15 PAPER→LIVE 승격 체크리스트 애플리케이션 계층.

`domain/promotion.py`의 순수 판정에 운영 상태(파일 저장 PAPER 이력) I/O를
연결한다. ADR-08-29-E 조건 2 충족 여부는 코드가 스스로 검증할 수 없는
실계좌 운영 사실이므로(미검증), 환경변수
`PERSONAL_ADR_0829E_CONDITION2_MET`(기본 false, fail-closed)로만 판단한다.
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
    """LIVE 전환 요청이 체크리스트 미충족으로 거부됨 — fail-closed."""

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
