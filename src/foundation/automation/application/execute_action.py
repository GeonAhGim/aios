"""Executes the action of a triggered rule. Notify goes straight through with
no gate; live-trading actions (order/hedge/kill) only reach
`AutomationActionSink` when `AutomationGatePort` returns ALLOW — a DENY or
missing evidence means zero sink calls (U-4 DoD adversarial test,
INVARIANTS.md I-09).

`FF_U4A_RULE_ENGINE` (task-6900) is checked first, ahead of the Notify
branch too -- while off, every action kind (including notify, which
otherwise bypasses the gate) is a no-op.
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.automation.contracts.v1 import (
    Action,
    HedgeAction,
    KillAction,
    NotifyAction,
    OrderAction,
)
from src.foundation.automation.flags import FEATURE_FLAG_NAME, flag_enabled
from src.foundation.automation.ports.action_sink import ActionResult, AutomationActionSink
from src.foundation.automation.ports.gate import ActionIntent, AutomationGatePort
from src.foundation.risk.ports.notifier import (
    PersonalNotification,
    PersonalNotificationKind,
    PersonalNotifierPort,
)

__all__ = ["execute_action"]


async def execute_action(
    *,
    tenant_id: UUID,
    rule_id: UUID,
    action: Action,
    trace_id: UUID,
    gate: AutomationGatePort,
    sink: AutomationActionSink,
    notifier: PersonalNotifierPort,
) -> ActionResult:
    if not flag_enabled():
        # Checked ahead of the NotifyAction branch too -- notify otherwise
        # bypasses the gate entirely, so a disabled flag must block it here
        # rather than rely on the gate/sink path below (U-4a DoD: zero
        # actions of any kind, order included, while the flag is off).
        return ActionResult(executed=False, detail="feature_disabled", error=FEATURE_FLAG_NAME)

    if isinstance(action, NotifyAction):
        result = await notifier.send(
            PersonalNotification(
                kind=PersonalNotificationKind.LIMIT_BREACH, message=action.message_template
            )
        )
        if not result.ok:
            return ActionResult(executed=False, detail="notify_failed", error=result.error)
        return ActionResult(executed=True, detail="notified")

    intent = ActionIntent(tenant_id=tenant_id, rule_id=rule_id, action=action, trace_id=trace_id)
    gate_decision = await gate.check(intent)
    if not gate_decision.allowed:
        reason = ",".join(gate_decision.reason_codes) or "GATE_DENIED"
        return ActionResult(executed=False, detail="gate_denied", error=reason)

    if isinstance(action, OrderAction):
        return await sink.submit_order(intent, gate_decision)
    if isinstance(action, HedgeAction):
        return await sink.submit_hedge(intent, gate_decision)
    if isinstance(action, KillAction):
        return await sink.trigger_kill(intent, gate_decision)
    raise AssertionError(f"unhandled action: {action!r}")  # pragma: no cover
