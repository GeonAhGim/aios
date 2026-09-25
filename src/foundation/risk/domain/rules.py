"""U-15 personal-mode order risk evaluation — pure rules, unit-testable
without DB/HTTP.

Spec: task-2749 DoD "bundle adversarial tests (over-limit REJECT, auto
kill)".
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
    """Today's realized P&L ratio. Negative = loss, e.g. Decimal("-0.02")
    == -2%."""


@dataclass(frozen=True)
class OrderRiskDecision:
    allowed: bool
    violations: tuple[PersonalRiskViolation, ...]
    should_kill: bool


def should_kill_for_daily_loss(
    bundle: PersonalRiskBundle, daily_realized_pnl_pct: Decimal
) -> bool:
    """task-6510 — split out of `evaluate_personal_order` so the periodic
    always-on monitor (`application/personal_daily_loss_monitor.py`) can
    reuse the exact same kill threshold without an `OrderRiskCheckInput`
    (it has no order to evaluate, only a daily P&L ratio)."""
    return daily_realized_pnl_pct <= -bundle.daily_loss_kill_pct


def evaluate_personal_order(
    bundle: PersonalRiskBundle, order: OrderRiskCheckInput
) -> OrderRiskDecision:
    """Fail-closed: if account equity is zero or negative, reject
    immediately without computing any ratio (avoids division by zero and,
    just as importantly, avoids an implicit allow when account state is
    unknown)."""
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

    should_kill = should_kill_for_daily_loss(bundle, order.daily_realized_pnl_pct)
    if should_kill:
        violations.append(PersonalRiskViolation.DAILY_LOSS_LIMIT_BREACHED)

    return OrderRiskDecision(
        allowed=not violations, violations=tuple(violations), should_kill=should_kill
    )
