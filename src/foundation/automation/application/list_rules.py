from __future__ import annotations

from uuid import UUID

from src.foundation.automation.contracts.v1 import AutomationRule, RuleStatus
from src.foundation.automation.ports.repository import AutomationRuleRepository

__all__ = ["list_rules"]


async def list_rules(
    repo: AutomationRuleRepository, *, tenant_id: UUID, status: RuleStatus | None = None
) -> tuple[AutomationRule, ...]:
    return await repo.list_by_tenant(tenant_id, status=status)
