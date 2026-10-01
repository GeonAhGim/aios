"""FD-14.2 (new) — Goal-based strategy generation wizard (StrategyWizardService).

Spec: ADR-2026-08-29-wallet-marketplace-dual-seller-strategy-authoring.md §3
— Implementation of goal-based wizard axis (without AI) from the decision
"easier high-level strategy creation than manually assembling conditions".
Generates and returns the condition schema already used by condition_compiler.py
and StrategyCreateRequest (PreviewCondition list + AND/OR combination) as-is
— without changing the execution engine or frontend condition editor, only
automatically determining "what to fill in". Returned values can be directly
filled into entry/exit/stop_loss fields of StrategyCreateRequest and saved via
existing POST /strategy-builder/strategies.

Deviation: stop_loss conditions here are indicator conditions (PreviewCondition)
rather than price-based % stop loss — volatility indicators like ATR scale
differently per asset (BTC vs DOGE), so the wizard cannot batch-generate fixed
thresholds and they are excluded from templates. Instead, oscillators
(RSI/CCI/WILLR/STOCH) have fixed scales (0~100 or -100~0), allowing same
thresholds regardless of asset — used as stop-loss signal for "momentum
continuing unfavorably".

3 (investment goal) x 3 (risk tolerance) = 9 templates defined as pure functions
— no AI calls for full predictability, always works regardless of Anthropic
credit status (natural-language prompt axis is in strategy_prompt_service.py).
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
    """Unknown goal/risk_tolerance — router converts to 400."""


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


# Per goal (entry threshold, exit threshold, stop-loss threshold) — higher risk tolerance:
# looser entry (more frequent), longer hold, later stop-loss.
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
            raise WizardError(f"Unknown investment goal: {goal}")
        if risk_tolerance not in RISK_TOLERANCES:
            raise WizardError(f"Unknown risk tolerance: {risk_tolerance}")

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
