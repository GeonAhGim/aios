"""FD-14.2(신설) — 목표기반 전략 생성 마법사 (StrategyWizardService).

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §3
— "조건식을 직접 조립하는 것보다 쉬운 고차원 전략 생성" 결정 중 AI 없는
축(목표기반 마법사) 구현. condition_compiler.py/StrategyCreateRequest가
이미 쓰는 조건 스키마(PreviewCondition 리스트 + AND/OR 결합)를 그대로
생성해서 반환한다 — 실행 엔진·프론트엔드 조건 에디터를 전혀 바꾸지
않고, "무엇을 채울지"만 자동으로 정해준다. 반환값을 그대로
StrategyCreateRequest의 entry/exit/stop_loss 필드에 채워 넣으면 기존
POST /strategy-builder/strategies로 저장할 수 있다.

편차: stop_loss 조건도 이 시스템에서는 가격 기반 %손절이 아니라 지표
조건(PreviewCondition)이다 — ATR 같은 변동성 지표는 자산마다 절대
스케일이 달라(BTC vs DOGE) 고정 임계값을 마법사가 일괄 생성할 수 없어
템플릿에서 제외했다. 대신 오실레이터(RSI/CCI/WILLR/STOCH)는 스케일이
0~100(또는 -100~0)으로 고정돼 자산에 무관하게 같은 임계값을 쓸 수
있다 — "모멘텀이 계속 불리하게 진행 중"이라는 손절 신호로 활용한다.

3(투자 목표) x 3(위험 허용도) = 9개 템플릿을 순수 함수로 고정 정의한다
— AI 호출이 전혀 없어 예측 가능하고, Anthropic 크레딧 상태와 무관하게
항상 동작한다(자연어 프롬프트 축은 strategy_prompt_service.py 참조).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.services.condition_evaluation import Operator
from src.services.preview_service import PreviewCondition

Goal = Literal["STEADY_GROWTH", "AGGRESSIVE_GROWTH", "HEDGE"]
RiskTolerance = Literal["LOW", "MEDIUM", "HIGH"]

GOALS: tuple[Goal, ...] = ("STEADY_GROWTH", "AGGRESSIVE_GROWTH", "HEDGE")
RISK_TOLERANCES: tuple[RiskTolerance, ...] = ("LOW", "MEDIUM", "HIGH")


class WizardError(Exception):
    """알 수 없는 goal/risk_tolerance — 라우터가 400으로 변환."""


class GeneratedConditions(BaseModel):
    entry_conditions: list[PreviewCondition]
    exit_conditions: list[PreviewCondition]
    stop_loss_conditions: list[PreviewCondition]
    entry_combine: Literal["AND", "OR"] = "AND"
    exit_combine: Literal["AND", "OR"] = "AND"
    stop_loss_combine: Literal["AND", "OR"] = "AND"
    explanation: str


def _rsi(threshold: float, operator: Operator) -> PreviewCondition:
    return PreviewCondition(
        indicator="RSI", params={"timeperiod": 14}, operator=operator, threshold=threshold
    )


def _cci(threshold: float, operator: Operator) -> PreviewCondition:
    return PreviewCondition(
        indicator="CCI", params={"timeperiod": 14}, operator=operator, threshold=threshold
    )


def _willr(threshold: float, operator: Operator) -> PreviewCondition:
    return PreviewCondition(
        indicator="WILLR", params={"timeperiod": 14}, operator=operator, threshold=threshold
    )


def _stoch(threshold: float, operator: Operator) -> PreviewCondition:
    return PreviewCondition(
        indicator="STOCH", params={"fastk_period": 14}, operator=operator, threshold=threshold
    )


def _macd(threshold: float, operator: Operator) -> PreviewCondition:
    return PreviewCondition(
        indicator="MACD", params={"slowperiod": 26}, operator=operator, threshold=threshold
    )


# 목표별 (진입 임계값, 청산 임계값, 손절 임계값) — 위험 허용도가 높을수록
# 진입은 느슨하게(더 자주 진입), 청산은 더 오래 들고가게, 손절은 더 늦게.
_STEADY_GROWTH: dict[RiskTolerance, tuple[float, float, float]] = {
    "LOW": (25.0, 65.0, 15.0),
    "MEDIUM": (30.0, 70.0, 18.0),
    "HIGH": (35.0, 75.0, 20.0),
}

_HEDGE: dict[RiskTolerance, tuple[float, float, float]] = {
    "LOW": (-85.0, -25.0, 10.0),
    "MEDIUM": (-80.0, -20.0, 15.0),
    "HIGH": (-75.0, -15.0, 20.0),
}

_AGGRESSIVE_STOP: dict[RiskTolerance, float] = {
    "LOW": -80.0,
    "MEDIUM": -100.0,
    "HIGH": -150.0,
}


class StrategyWizardService:
    def generate(self, goal: str, risk_tolerance: str) -> GeneratedConditions:
        if goal not in GOALS:
            raise WizardError(f"알 수 없는 투자 목표입니다: {goal}")
        if risk_tolerance not in RISK_TOLERANCES:
            raise WizardError(f"알 수 없는 위험 허용도입니다: {risk_tolerance}")

        if goal == "STEADY_GROWTH":
            entry_th, exit_th, stop_th = _STEADY_GROWTH[risk_tolerance]
            return GeneratedConditions(
                entry_conditions=[_rsi(entry_th, "<")],
                exit_conditions=[_rsi(exit_th, ">")],
                stop_loss_conditions=[_rsi(stop_th, "<")],
                explanation=(
                    f"RSI가 {entry_th:g} 밑으로 떨어지면(과매도) 매수하고, "
                    f"{exit_th:g} 위로 오르면(과매수) 매도합니다. 진입 후에도 RSI가 "
                    f"{stop_th:g} 밑까지 더 떨어지면 하락이 계속된다고 보고 손절합니다."
                ),
            )
        if goal == "AGGRESSIVE_GROWTH":
            stop_th = _AGGRESSIVE_STOP[risk_tolerance]
            return GeneratedConditions(
                entry_conditions=[_macd(0.0, "crosses_above")],
                exit_conditions=[_macd(0.0, "crosses_below")],
                stop_loss_conditions=[_cci(stop_th, "<")],
                explanation=(
                    "MACD가 0선을 상향 돌파하면(상승 모멘텀 시작) 매수하고, "
                    f"다시 0선 아래로 내려가면 매도합니다. CCI가 {stop_th:g} 밑으로 "
                    "떨어지면 추세가 강하게 꺾였다고 보고 손절합니다."
                ),
            )
        # HEDGE
        entry_th, exit_th, stop_th = _HEDGE[risk_tolerance]
        return GeneratedConditions(
            entry_conditions=[_willr(entry_th, "crosses_below")],
            exit_conditions=[_willr(exit_th, "crosses_above")],
            stop_loss_conditions=[_stoch(stop_th, "<")],
            explanation=(
                f"Williams %R이 {entry_th:g} 밑으로 떨어지면(극단적 과매도) 반등을 "
                f"노리고 매수하고, {exit_th:g} 위로 오르면 바로 차익실현합니다. "
                f"스토캐스틱(%K)이 {stop_th:g} 밑까지 떨어지면 반등 없이 계속 하락 "
                "중이라고 보고 손절합니다."
            ),
        )
