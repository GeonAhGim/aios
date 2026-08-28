"""15.1 — 적합성평가 설문 (SuitabilityQuestionnaire).

Spec: 기능설계문서_v1.20.md#FD-15.1

법적 지위: 아래 문항·점수·기준치는 전부 Draft다 — 18.3/19장 법률검토가
완료되기 전까지 법적 구속력 있는 기준으로 취급하지 않는다(FD-15 원문
경고 그대로). 지금은 UX 골격만 확정한다.

용도(2026-08-10 확정): 강제 차단이 아니라 ①조언 참고 ②본인 성향과
어긋나는 정책·전략 실행 시 경고(FD-15.3) 용도로만 쓴다.

5개 문항 각각 0~3점, 합산 0~15점을 3단계 위험등급으로 매핑(Draft
경계값): 0~5 안정형, 6~10 중립형, 11~15 공격형.
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
    investable_ratio_pct: int  # 순자산 대비 투자가능 비중(%)
    loss_tolerance_pct: int  # 원금 대비 손실 감내 수준(%)
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
