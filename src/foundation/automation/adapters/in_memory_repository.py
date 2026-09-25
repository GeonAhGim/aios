"""In-memory implementation for tests and follow-up-leaf development. The
Postgres adapter (persistence) is wired in a follow-up leaf together with the
API router and migration (task-2631 decision).
"""

from __future__ import annotations

from uuid import UUID

from src.foundation.automation.contracts.v1 import AutomationRule, RuleStatus

__all__ = ["InMemoryAutomationRuleRepository"]


class InMemoryAutomationRuleRepository:
    def __init__(self) -> None:
        self._rules: dict[tuple[UUID, UUID], AutomationRule] = {}

    async def save(self, rule: AutomationRule) -> None:
        self._rules[(rule.tenant_id, rule.rule_id)] = rule

    async def get(self, tenant_id: UUID, rule_id: UUID) -> AutomationRule | None:
        rule = self._rules.get((tenant_id, rule_id))
        if rule is None or rule.tenant_id != tenant_id:
            return None
        return rule

    async def list_by_tenant(
        self, tenant_id: UUID, *, status: RuleStatus | None = None
    ) -> tuple[AutomationRule, ...]:
        return tuple(
            rule
            for (t, _), rule in self._rules.items()
            if t == tenant_id and (status is None or rule.status == status)
        )
