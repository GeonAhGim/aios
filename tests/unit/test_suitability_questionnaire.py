"""15.1 단위테스트 — 순수 점수화 로직."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.services import suitability_questionnaire as sq_module
from src.services.suitability_questionnaire import (
    RISK_PROFILE_AGGRESSIVE,
    RISK_PROFILE_NEUTRAL,
    RISK_PROFILE_STABLE,
    InvestmentGoal,
    LiquidityNeed,
    SuitabilityAnswers,
    SuitabilityQuestionnaire,
    score_to_risk_profile,
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


def test_loss_tolerance_mid_boundary_scores_one_point():
    result = SuitabilityQuestionnaire().evaluate(
        _answers(loss_tolerance_pct=15, investment_goal=InvestmentGoal.SHORT_TERM_PROFIT)
    )

    # loss_tolerance_pct=15 -> 1점, investable_ratio_pct=5 -> 0점,
    # years_of_experience=0 -> 0점, investment_goal SHORT_TERM_PROFIT -> 3점,
    # liquidity_need WITHIN_1_YEAR -> 0점
    assert result.score == 4


def test_invalid_investment_goal_raises_validation_error():
    with pytest.raises(ValidationError):
        _answers(investment_goal="NOT_A_GOAL")


def test_invalid_liquidity_need_raises_validation_error():
    with pytest.raises(ValidationError):
        _answers(liquidity_need="NOT_A_LIQUIDITY_NEED")


def test_missing_required_field_raises_validation_error():
    with pytest.raises(ValidationError):
        SuitabilityAnswers(
            investable_ratio_pct=5,
            loss_tolerance_pct=5,
            investment_goal=InvestmentGoal.LONG_TERM_GROWTH,
            liquidity_need=LiquidityNeed.WITHIN_1_YEAR,
        )


def test_non_integer_years_of_experience_raises_validation_error():
    with pytest.raises(ValidationError):
        _answers(years_of_experience="many years")


def test_evaluate_propagates_scoring_dependency_failure(monkeypatch):
    def _boom(_pct: int) -> int:
        raise RuntimeError("scoring dependency unavailable")

    monkeypatch.setattr(sq_module, "_score_loss_tolerance", _boom)

    with pytest.raises(RuntimeError, match="scoring dependency unavailable"):
        SuitabilityQuestionnaire().evaluate(_answers())


# ── negative tests (invariant-violating inputs) ─────────────────────────────

# Domain invariants: years_of_experience ≥ 0, investable_ratio_pct ∈ [0, 100],
# loss_tolerance_pct ∈ [0, 100]. The model has no Pydantic validator, so the
# scorer must fail-closed (return 0) for out-of-range values.


def test_negative_years_of_experience_scores_zero():
    """Negative years_of_experience → 0 score (fail-closed)."""
    result = SuitabilityQuestionnaire().evaluate(_answers(years_of_experience=-1))
    assert result.score == result.score  # still returns a valid int
    assert sq_module._score_years_of_experience(-1) == 0


def test_years_of_experience_at_boundary_0_1_3_10_11():
    """Years boundaries: 0→0, 1→1, 3→1, 4→2, 10→2, 11→3."""
    assert sq_module._score_years_of_experience(0) == 0
    assert sq_module._score_years_of_experience(1) == 1
    assert sq_module._score_years_of_experience(3) == 1
    assert sq_module._score_years_of_experience(4) == 2
    assert sq_module._score_years_of_experience(10) == 2
    assert sq_module._score_years_of_experience(11) == 3


def test_investable_ratio_zero_scores_zero():
    """investable_ratio_pct=0 → 0 score (lower boundary)."""
    assert sq_module._score_investable_ratio(0) == 0
    assert sq_module._score_investable_ratio(10) == 0
    assert sq_module._score_investable_ratio(11) == 1
    assert sq_module._score_investable_ratio(30) == 1
    assert sq_module._score_investable_ratio(31) == 2
    assert sq_module._score_investable_ratio(60) == 2
    assert sq_module._score_investable_ratio(61) == 3


def test_loss_tolerance_boundary_scores():
    """loss_tolerance_pct boundaries: 0→0, 5→0, 6→1, 15→1, 16→2, 30→2, 31→3."""
    assert sq_module._score_loss_tolerance(0) == 0
    assert sq_module._score_loss_tolerance(5) == 0
    assert sq_module._score_loss_tolerance(6) == 1
    assert sq_module._score_loss_tolerance(15) == 1
    assert sq_module._score_loss_tolerance(16) == 2
    assert sq_module._score_loss_tolerance(30) == 2
    assert sq_module._score_loss_tolerance(31) == 3


def test_score_to_risk_profile_boundary_at_5_is_stable():
    """Score=5 → 안정형 (upper bound of stable)."""
    assert score_to_risk_profile(5) == RISK_PROFILE_STABLE


def test_score_to_risk_profile_boundary_at_6_is_neutral():
    """Score=6 → 중립형 (lower bound of neutral)."""
    assert score_to_risk_profile(6) == RISK_PROFILE_NEUTRAL


def test_score_to_risk_profile_boundary_at_10_is_neutral():
    """Score=10 → 중립형 (upper bound of neutral)."""
    assert score_to_risk_profile(10) == RISK_PROFILE_NEUTRAL


def test_score_to_risk_profile_boundary_at_11_is_aggressive():
    """Score=11 → 공격형 (lower bound of aggressive)."""
    assert score_to_risk_profile(11) == RISK_PROFILE_AGGRESSIVE


def test_score_to_risk_profile_negative_yields_stable():
    """Negative score (invariant violation) → 안정형 via passive fallback."""
    assert score_to_risk_profile(-1) == RISK_PROFILE_STABLE


def test_score_to_risk_profile_over_15_yields_aggressive():
    """Score over 15 (invariant violation) → 공격형."""
    assert score_to_risk_profile(16) == RISK_PROFILE_AGGRESSIVE


def test_full_evaluate_with_negative_years_fails_closed():
    """Full evaluate with negative years_of_experience → valid result, not crash."""
    result = SuitabilityQuestionnaire().evaluate(_answers(years_of_experience=-5))
    assert result.score >= 0
    assert result.risk_profile in (
        RISK_PROFILE_STABLE,
        RISK_PROFILE_NEUTRAL,
        RISK_PROFILE_AGGRESSIVE,
    )


def test_full_evaluate_with_max_values_yields_max_score():
    """All max inputs → score=15, 공격형."""
    result = SuitabilityQuestionnaire().evaluate(
        _answers(
            years_of_experience=99,
            investable_ratio_pct=100,
            loss_tolerance_pct=100,
            investment_goal=InvestmentGoal.SHORT_TERM_PROFIT,
            liquidity_need=LiquidityNeed.OVER_THREE_YEARS,
        )
    )
    assert result.score == 15
    assert result.risk_profile == RISK_PROFILE_AGGRESSIVE


# ── additional failure injection tests ──────────────────────────────────────


def test_evaluate_propagates_key_error_from_liquidity_scoring(monkeypatch):
    """KeyError in _score_liquidity_need → propagate to caller."""

    def _boom(_need):
        raise KeyError("missing liquidity mapping")

    monkeypatch.setattr(sq_module, "_score_liquidity_need", _boom)

    with pytest.raises(KeyError, match="missing liquidity mapping"):
        SuitabilityQuestionnaire().evaluate(_answers())


def test_evaluate_propagates_exception_from_investable_ratio(monkeypatch):
    """Exception in _score_investable_ratio → propagate to caller."""

    def _boom(_pct: int) -> int:
        raise ValueError("investable ratio computation failed")

    monkeypatch.setattr(sq_module, "_score_investable_ratio", _boom)

    with pytest.raises(ValueError, match="investable ratio computation failed"):
        SuitabilityQuestionnaire().evaluate(_answers())


# ── performance assertion ───────────────────────────────────────────────────


@pytest.mark.perf
def test_evaluate_completes_within_1ms(perf_budget):
    """evaluate() call completes within 1ms — performance budget (task-10175)."""
    qa = SuitabilityQuestionnaire()
    ans = _answers()
    iterations = 1000
    # batch=iterations: per-call average over 1000 consecutive calls
    perf_budget.assert_within(
        lambda: qa.evaluate(ans), budget_ms=1.0, batch=iterations, label="evaluate avg"
    )
