"""Executes the action of a triggered rule. Notify goes straight through with
no gate; live-trading actions (order/hedge/kill) only reach
`AutomationActionSink` when `AutomationGatePort` returns ALLOW — a DENY or
missing evidence means zero sink calls (U-4 DoD adversarial test,
INVARIANTS.md I-09).
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
