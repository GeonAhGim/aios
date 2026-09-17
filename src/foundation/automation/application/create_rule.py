from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.foundation.automation.contracts.v1 import Action, AutomationRule, Condition, RuleStatus
from src.foundation.automation.ports.repository import AutomationRuleRepository

__all__ = ["create_rule"]


async def create_rule(
    repo: AutomationRuleRepository,
    *,
    tenant_id: UUID,
    name: str,
    conditions: tuple[Condition, ...],
    action: Action,
) -> AutomationRule:
    now = datetime.now(timezone.utc)
    rule = AutomationRule(
        rule_id=uuid4(),
        tenant_id=tenant_id,
        name=name,
        conditions=conditions,
        action=action,
        status=RuleStatus.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    await repo.save(rule)
    return rule
