"""U-15 개인 운영 주문 리스크 평가 순수 규칙 — DB/HTTP 없이 단위 테스트
가능해야 한다.

Spec: task-2749 DoD "번들 적대 테스트(한도 초과 REJECT·kill 자동 발동)".
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from src.foundation.risk.domain.models import PersonalRiskBundle


class PersonalRiskViolation(str, Enum):
    ACCOUNT_STATE_INVALID = "ACCOUNT_STATE_INVALID"
    SYMBOL_NOT_WHITELISTED = "SYMBOL_NOT_WHITELISTED"
    POSITION_SIZE_EXCEEDED = "POSITION_SIZE_EXCEEDED"
    NOTIONAL_CAP_EXCEEDED = "NOTIONAL_CAP_EXCEEDED"
    EXPOSURE_LIMIT_EXCEEDED = "EXPOSURE_LIMIT_EXCEEDED"
    DAILY_LOSS_LIMIT_BREACHED = "DAILY_LOSS_LIMIT_BREACHED"


@dataclass(frozen=True)
class OrderRiskCheckInput:
    exchange: str
    symbol: str
    order_notional_krw: Decimal
    account_equity_krw: Decimal
    current_exposure_krw: Decimal
    daily_realized_pnl_pct: Decimal
    """오늘 실현손익률. 음수 = 손실, 예: Decimal("-0.02") == -2%."""


@dataclass(frozen=True)
class OrderRiskDecision:
    allowed: bool
    violations: tuple[PersonalRiskViolation, ...]
    should_kill: bool


def evaluate_personal_order(
    bundle: PersonalRiskBundle, order: OrderRiskCheckInput
) -> OrderRiskDecision:
    """fail-closed: 계좌 자본이 0 이하면 비율 계산 없이 즉시 거부한다(0으로
    나누기를 방지하는 동시에, 계좌 상태를 알 수 없을 때 암묵적 허용을
    만들지 않는다)."""
    if order.account_equity_krw <= 0:
        return OrderRiskDecision(
            allowed=False,
            violations=(PersonalRiskViolation.ACCOUNT_STATE_INVALID,),
            should_kill=False,
        )

    violations: list[PersonalRiskViolation] = []

    if not bundle.symbol_whitelist or order.symbol not in bundle.symbol_whitelist:
        violations.append(PersonalRiskViolation.SYMBOL_NOT_WHITELISTED)

    position_pct = order.order_notional_krw / order.account_equity_krw
    if position_pct > bundle.position_pct_of_equity:
        violations.append(PersonalRiskViolation.POSITION_SIZE_EXCEEDED)

    notional_cap = bundle.notional_cap_for(order.exchange)
    if order.order_notional_krw > notional_cap:
        violations.append(PersonalRiskViolation.NOTIONAL_CAP_EXCEEDED)

    projected_exposure_pct = (
        order.current_exposure_krw + order.order_notional_krw
    ) / order.account_equity_krw
    if projected_exposure_pct > bundle.max_exposure_pct:
        violations.append(PersonalRiskViolation.EXPOSURE_LIMIT_EXCEEDED)

    should_kill = order.daily_realized_pnl_pct <= -bundle.daily_loss_kill_pct
    if should_kill:
        violations.append(PersonalRiskViolation.DAILY_LOSS_LIMIT_BREACHED)

    return OrderRiskDecision(
        allowed=not violations, violations=tuple(violations), should_kill=should_kill
    )
