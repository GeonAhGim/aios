"""Rule repository port. This leaf does not wire a DB adapter (split into a
follow-up leaf together with the `rules.py` API router and migration —
task-2631 decision). Tests satisfy this Protocol with the fake in
`adapters/in_memory_repository.py`.
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from src.foundation.automation.contracts.v1 import AutomationRule, RuleStatus

__all__ = ["AutomationRuleRepository"]


class AutomationRuleRepository(Protocol):
    async def save(self, rule: AutomationRule) -> None: ...

    async def get(self, tenant_id: UUID, rule_id: UUID) -> AutomationRule | None:
        """Must return `None` when `tenant_id` doesn't match (blocks cross-tenant reads)."""
        ...

    async def list_by_tenant(
        self, tenant_id: UUID, *, status: RuleStatus | None = None
    ) -> tuple[AutomationRule, ...]: ...
