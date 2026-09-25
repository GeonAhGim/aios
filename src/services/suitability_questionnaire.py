"""15.1 — Suitability Questionnaire (SuitabilityQuestionnaire).

Legacy spec: FD-15.1 (design doc v1.20) — superseded by L4_*.md specs.

Legal status: All items, scores, and thresholds below are Draft — do not
treat them as legally binding until the legal review in sections 18.3/19
is complete (per the FD-15 original warning). For now, only the UX
skeleton is finalised.

Use case (confirmed 2026-08-10): Used solely for ① advisory reference
and ② warning when executing a policy or strategy that conflicts with
the user's own risk profile (FD-15.3). Not a hard block.

Scoring: 5 items, each 0-3 points, total 0-15 mapped to 3 risk tiers
(Draft thresholds): 0-5 Stable, 6-10 Neutral, 11-15 Aggressive.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel

RISK_PROFILE_STABLE = "안정형"
RISK_PROFILE_NEUTRAL = "중립형"
RISK_PROFILE_AGGRESSIVE = "공격형"


class InvestmentGoal(str, Enum):
    SHORT_TERM_PROFIT = "SHORT_TERM_PROFIT"
    LONG_TERM_GROWTH = "LONG_TERM_GROWTH"


class LiquidityNeed(str, Enum):
    WITHIN_1_YEAR = "WITHIN_1_YEAR"
    ONE_TO_THREE_YEARS = "1_TO_3_YEARS"
    OVER_THREE_YEARS = "OVER_3_YEARS"


class SuitabilityAnswers(BaseModel):
    years_of_experience: int  # 0, 1~3, 4~10, 10+
    investable_ratio_pct: int  # Investable assets as % of net worth
    loss_tolerance_pct: int  # Maximum loss tolerance as % of principal
    investment_goal: InvestmentGoal
    liquidity_need: LiquidityNeed


class SuitabilityResult(BaseModel):
    score: int
    risk_profile: str
    answers: SuitabilityAnswers


def _score_years_of_experience(years: int) -> int:
    if years <= 0:
        return 0
    if years <= 3:
        return 1
    if years <= 10:
        return 2
    return 3


def _score_investable_ratio(pct: int) -> int:
    if pct <= 10:
        return 0
    if pct <= 30:
        return 1
    if pct <= 60:
        return 2
    return 3


def _score_loss_tolerance(pct: int) -> int:
    if pct <= 5:
        return 0
    if pct <= 15:
        return 1
    if pct <= 30:
        return 2
    return 3


def _score_investment_goal(goal: InvestmentGoal) -> int:
    return 3 if goal == InvestmentGoal.SHORT_TERM_PROFIT else 1


def _score_liquidity_need(need: LiquidityNeed) -> int:
    return {
        LiquidityNeed.WITHIN_1_YEAR: 0,
        LiquidityNeed.ONE_TO_THREE_YEARS: 1,
        LiquidityNeed.OVER_THREE_YEARS: 3,
    }[need]


def score_to_risk_profile(score: int) -> str:
    if score <= 5:
        return RISK_PROFILE_STABLE
    if score <= 10:
        return RISK_PROFILE_NEUTRAL
    return RISK_PROFILE_AGGRESSIVE


class SuitabilityQuestionnaire:
    def evaluate(self, answers: SuitabilityAnswers) -> SuitabilityResult:
        score = (
            _score_years_of_experience(answers.years_of_experience)
            + _score_investable_ratio(answers.investable_ratio_pct)
            + _score_loss_tolerance(answers.loss_tolerance_pct)
            + _score_investment_goal(answers.investment_goal)
            + _score_liquidity_need(answers.liquidity_need)
        )
        return SuitabilityResult(
            score=score, risk_profile=score_to_risk_profile(score), answers=answers
        )
