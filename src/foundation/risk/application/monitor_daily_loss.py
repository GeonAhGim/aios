"""task-6510 (XREV task-3820 REJECT fix, finding id=149) — the order-free
daily-loss kill check.

`evaluate_personal_order`/`check_personal_order`'s kill decision only ever
ran inside an order's pre-submit evaluation (`foundation_personal_gate.
evaluate_personal_layer`) -- with no order in flight, the 3% daily-loss
auto-kill could never fire in prod, since nothing supplied a
`personal_risk_snapshot` between orders (task-3986's own known-gap note).
This module reuses the same kill threshold (`should_kill_for_daily_loss`)
and the same state/notifier side effects as `check_personal_order`'s kill
block (no reimplementation), driven by a periodic caller
(`personal_daily_loss_monitor.py`) instead of an order.
"""

from __future__ import annotations

from decimal import Decimal

from src.foundation.risk.domain.models import PersonalRiskBundle
from src.foundation.risk.domain.rules import should_kill_for_daily_loss
from src.foundation.risk.ports.notifier import (
    PersonalNotification,
    PersonalNotificationKind,
    PersonalNotifierPort,
)
from src.foundation.risk.ports.state import PersonalOperationStatePort


async def check_personal_daily_loss(
    bundle: PersonalRiskBundle,
    *,
    account_equity_krw: Decimal,
    daily_realized_pnl_pct: Decimal,
    notifier: PersonalNotifierPort,
    state: PersonalOperationStatePort,
) -> bool:
    """Returns whether kill is engaged (by this call or already). Mirrors
    `evaluate_personal_order`'s fail-closed guard: non-positive equity
    means the ratio's denominator is untrustworthy, so this returns
    `False` without touching kill state rather than guessing either way."""
    if account_equity_krw <= 0:
        return False
    if not should_kill_for_daily_loss(bundle, daily_realized_pnl_pct):
        return False
    if await state.is_kill_engaged():
        return True
    await state.engage_kill(reason="DAILY_LOSS_LIMIT_BREACHED")
    await notifier.send(
        PersonalNotification(
            kind=PersonalNotificationKind.KILL_SWITCH,
            message="Daily loss limit breached - kill switch auto-engaged (periodic monitor)",
        )
    )
    return True
