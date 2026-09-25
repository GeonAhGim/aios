"""Batch-evaluate active rules. Performance budget: 100 rules p95 < 1s (U-4 DoD)
— measured by `tests/foundation/unit/automation/test_evaluate_rules_perf.py`.

`FF_U4A_RULE_ENGINE` (task-6900) off -- returns `()` before even reading the
repo, so a disabled engine never evaluates, let alone triggers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from src.foundation.automation.contracts.v1 import Action, RuleStatus
from src.foundation.automation.domain.evaluate import MarketSnapshot, evaluate_rule
from src.foundation.automation.flags import flag_enabled
from src.foundation.automation.ports.repository import AutomationRuleRepository

__all__ = ["TriggeredRule", "evaluate_active_rules"]

_EMPTY_SNAPSHOTS: Mapping[str, MarketSnapshot] = MappingProxyType({})


@dataclass(frozen=True, slots=True)
class TriggeredRule:
    rule_id: UUID
    action: Action


async def evaluate_active_rules(
    repo: AutomationRuleRepository,
    *,
    tenant_id: UUID,
    snapshots: Mapping[str, MarketSnapshot],
    prev_snapshots: Mapping[str, MarketSnapshot] = _EMPTY_SNAPSHOTS,
) -> tuple[TriggeredRule, ...]:
    if not flag_enabled():
        # Fail-closed no-op -- a batch loop over many tenants' rules must not
        # raise on a disabled flag, and skipping the repo call altogether
        # means a disabled engine never even reads rules, let alone fires
        # them (U-4a DoD: "evaluation never triggers actions").
        return ()
    rules = await repo.list_by_tenant(tenant_id, status=RuleStatus.ACTIVE)
    return tuple(
        TriggeredRule(rule_id=rule.rule_id, action=rule.action)
        for rule in rules
        if evaluate_rule(rule, snapshots, prev_snapshots)
    )
