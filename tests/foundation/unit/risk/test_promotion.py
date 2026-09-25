"""U-15 PAPER→LIVE 승격 체크리스트 순수 규칙 단위테스트 — DB 없음."""

from __future__ import annotations

from src.foundation.risk.domain.promotion import (
    MIN_PAPER_DAYS,
    PromotionBlocker,
    PromotionChecklistInput,
    evaluate_promotion_checklist,
)


def _input(
    *,
    adr_0829e_condition2_met: bool = True,
    bundle_active: bool = True,
    paper_days_elapsed: int = MIN_PAPER_DAYS,
    paper_violation_count: int = 0,
) -> PromotionChecklistInput:
    return PromotionChecklistInput(
        adr_0829e_condition2_met=adr_0829e_condition2_met,
        bundle_active=bundle_active,
        paper_days_elapsed=paper_days_elapsed,
        paper_violation_count=paper_violation_count,
    )


def test_all_conditions_met_is_eligible():
    result = evaluate_promotion_checklist(_input())

    assert result.eligible is True
    assert result.blockers == ()


def test_condition2_unmet_blocks_promotion():
    result = evaluate_promotion_checklist(_input(adr_0829e_condition2_met=False))

    assert result.eligible is False
    assert PromotionBlocker.ADR_0829E_CONDITION2_UNMET in result.blockers


def test_bundle_inactive_blocks_promotion():
    result = evaluate_promotion_checklist(_input(bundle_active=False))

    assert result.eligible is False
    assert PromotionBlocker.BUNDLE_NOT_ACTIVE in result.blockers


def test_fewer_than_7_paper_days_blocks_promotion():
    result = evaluate_promotion_checklist(_input(paper_days_elapsed=MIN_PAPER_DAYS - 1))

    assert result.eligible is False
    assert PromotionBlocker.INSUFFICIENT_PAPER_HISTORY in result.blockers


def test_any_paper_violation_blocks_promotion():
    result = evaluate_promotion_checklist(_input(paper_violation_count=1))

    assert result.eligible is False
    assert PromotionBlocker.PAPER_VIOLATIONS_PRESENT in result.blockers


def test_all_conditions_unmet_reports_all_blockers():
    result = evaluate_promotion_checklist(
        _input(
            adr_0829e_condition2_met=False,
            bundle_active=False,
            paper_days_elapsed=0,
            paper_violation_count=3,
        )
    )

    assert result.eligible is False
    assert set(result.blockers) == {
        PromotionBlocker.ADR_0829E_CONDITION2_UNMET,
        PromotionBlocker.BUNDLE_NOT_ACTIVE,
        PromotionBlocker.INSUFFICIENT_PAPER_HISTORY,
        PromotionBlocker.PAPER_VIOLATIONS_PRESENT,
    }
