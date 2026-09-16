"""U-15 PAPER→LIVE 승격 체크리스트 순수 규칙.

Spec: task-2749 — "ADR-08-29-E 개정 조건 + 이 번들 활성 + 7일 PAPER 무위반".
ADR-2026-08-29-E §15.6-D 세 조건 중 조건 2("4.9 Human Approval security
(MFA+dual-approval)가 실계좌에 실제 적용됨")만 LIVE를 막는다(다른 두 조건은
이미 충족/과충족, 그 ADR §참조). 이 모듈은 조건 2가 "실제로 충족됐다"는
사실 자체를 검증하지 않는다 — 실계좌 운영 사실은 코드가 스스로 알 수 없다
(미검증); 호출자가 운영자 attestation 값을 넘긴다.
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
    """fail-closed: 각 조건은 독립적으로 검사되고, 하나라도 충족되지 않으면
    eligible은 False다 — 부분 충족으로 승격을 허용하는 절충은 없다."""
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
