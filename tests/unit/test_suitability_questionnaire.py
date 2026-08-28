"""15.1 단위테스트 — 순수 점수화 로직."""
from src.services.suitability_questionnaire import (
    RISK_PROFILE_AGGRESSIVE,
    RISK_PROFILE_NEUTRAL,
    RISK_PROFILE_STABLE,
    InvestmentGoal,
    LiquidityNeed,
    SuitabilityAnswers,
    SuitabilityQuestionnaire,
)


def _answers(**overrides):
    defaults = {
        "years_of_experience": 0,
        "investable_ratio_pct": 5,
        "loss_tolerance_pct": 5,
        "investment_goal": InvestmentGoal.LONG_TERM_GROWTH,
        "liquidity_need": LiquidityNeed.WITHIN_1_YEAR,
    }
    defaults.update(overrides)
    return SuitabilityAnswers(**defaults)


def test_all_lowest_answers_yield_stable_profile():
    result = SuitabilityQuestionnaire().evaluate(_answers())

    assert result.risk_profile == RISK_PROFILE_STABLE
    assert result.score == 1  # investment_goal LONG_TERM_GROWTH 기본 1점


def test_all_highest_answers_yield_aggressive_profile():
    result = SuitabilityQuestionnaire().evaluate(
        _answers(
            years_of_experience=15,
            investable_ratio_pct=80,
            loss_tolerance_pct=50,
            investment_goal=InvestmentGoal.SHORT_TERM_PROFIT,
            liquidity_need=LiquidityNeed.OVER_THREE_YEARS,
        )
    )

    assert result.risk_profile == RISK_PROFILE_AGGRESSIVE
    assert result.score == 15


def test_mid_range_answers_yield_neutral_profile():
    result = SuitabilityQuestionnaire().evaluate(
        _answers(
            years_of_experience=5,
            investable_ratio_pct=40,
            loss_tolerance_pct=20,
            investment_goal=InvestmentGoal.LONG_TERM_GROWTH,
            liquidity_need=LiquidityNeed.ONE_TO_THREE_YEARS,
        )
    )

    assert result.risk_profile == RISK_PROFILE_NEUTRAL


def test_score_boundaries_are_inclusive_on_lower_band():
    result = SuitabilityQuestionnaire().evaluate(
        _answers(
            years_of_experience=1,  # 1점
            investable_ratio_pct=15,  # 1점
            loss_tolerance_pct=5,  # 0점
            investment_goal=InvestmentGoal.LONG_TERM_GROWTH,  # 1점
            liquidity_need=LiquidityNeed.WITHIN_1_YEAR,  # 0점
        )
    )

    assert result.score == 3
    assert result.risk_profile == RISK_PROFILE_STABLE
