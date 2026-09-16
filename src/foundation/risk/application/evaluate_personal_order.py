"""U-15 order risk-gate application layer — attaches notification/kill
state I/O to the pure domain decision.

Spec: task-2749 DoD "bundle adversarial tests (over-limit REJECT, auto
kill)".
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.foundation.risk.domain.models import PersonalRiskBundle
from src.foundation.risk.domain.rules import (
    OrderRiskCheckInput,
    OrderRiskDecision,
    evaluate_personal_order,
)
from src.foundation.risk.ports.notifier import (
    PersonalNotification,
    PersonalNotificationKind,
    PersonalNotifierPort,
)
from src.foundation.risk.ports.state import PersonalOperationStatePort


async def check_personal_order(
    bundle: PersonalRiskBundle,
    order: OrderRiskCheckInput,
    *,
    notifier: PersonalNotifierPort,
    state: PersonalOperationStatePort,
) -> OrderRiskDecision:
    """On any violation, record it and send an alert. If the daily-loss
    limit is breached (and kill is not already engaged), engage the kill
    switch and send a separate alert — the two alerts are distinct reasons
    and are not merged."""
    decision = evaluate_personal_order(bundle, order)

    if decision.violations:
        reasons = ", ".join(v.value for v in decision.violations)
        await state.record_violation(occurred_on=datetime.now(timezone.utc).date())
        await notifier.send(
            PersonalNotification(
                kind=PersonalNotificationKind.LIMIT_BREACH,
                message=f"Order rejected for {order.exchange}:{order.symbol} - {reasons}",
            )
        )

    if decision.should_kill and not await state.is_kill_engaged():
        await state.engage_kill(reason="DAILY_LOSS_LIMIT_BREACHED")
        await notifier.send(
            PersonalNotification(
                kind=PersonalNotificationKind.KILL_SWITCH,
                message="Daily loss limit breached - kill switch auto-engaged",
            )
        )

    return decision
