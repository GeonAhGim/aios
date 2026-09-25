from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from src.foundation.automation.contracts.v1 import AutomationRule, RuleNotFoundError, RuleStatus
from src.foundation.automation.flags import require_flag_enabled
from src.foundation.automation.ports.repository import AutomationRuleRepository

__all__ = ["cancel_rule"]


async def cancel_rule(
    repo: AutomationRuleRepository, *, tenant_id: UUID, rule_id: UUID
) -> AutomationRule:
    require_flag_enabled()
    rule = await repo.get(tenant_id, rule_id)
    # Block again here even if a buggy adapter returns a cross-tenant row
    # (fail-closed, defensive double-check).
    if rule is None or rule.tenant_id != tenant_id:
        raise RuleNotFoundError(str(rule_id))
    cancelled = rule.model_copy(
        update={"status": RuleStatus.CANCELLED, "updated_at": datetime.now(timezone.utc)}
    )
    await repo.save(cancelled)
    return cancelled
