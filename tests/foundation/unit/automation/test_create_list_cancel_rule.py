from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.automation.adapters.in_memory_repository import InMemoryAutomationRuleRepository
from src.foundation.automation.application.cancel_rule import cancel_rule
from src.foundation.automation.application.create_rule import create_rule
from src.foundation.automation.application.list_rules import list_rules
from src.foundation.automation.contracts.v1 import (
    AutomationRule,
    NotifyAction,
    PriceCondition,
    RuleNotFoundError,
    RuleStatus,
)


def _condition() -> PriceCondition:
    return PriceCondition(symbol="005930", operator=">", threshold=Decimal("100"))


def _action() -> NotifyAction:
    return NotifyAction(message_template="price above 100")


@pytest.mark.asyncio
async def test_create_and_list_round_trip(tenant_id) -> None:
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo, tenant_id=tenant_id, name="rule-1", conditions=(_condition(),), action=_action()
    )
    listed = await list_rules(repo, tenant_id=tenant_id)
    assert listed == (created,)
    assert created.status == RuleStatus.ACTIVE


@pytest.mark.asyncio
async def test_list_is_tenant_isolated(tenant_id) -> None:
    repo = InMemoryAutomationRuleRepository()
    other_tenant = uuid4()
    await create_rule(
        repo, tenant_id=tenant_id, name="rule-1", conditions=(_condition(),), action=_action()
    )
    listed_for_other = await list_rules(repo, tenant_id=other_tenant)
    assert listed_for_other == ()


@pytest.mark.asyncio
async def test_cancel_rule_sets_status(tenant_id) -> None:
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo, tenant_id=tenant_id, name="rule-1", conditions=(_condition(),), action=_action()
    )
    cancelled = await cancel_rule(repo, tenant_id=tenant_id, rule_id=created.rule_id)
    assert cancelled.status == RuleStatus.CANCELLED


@pytest.mark.asyncio
async def test_cancel_rule_cross_tenant_denied(tenant_id) -> None:
    """적대적 테스트: 다른 테넌트가 rule_id만 알아내도 취소도 존재 확인도 못 한다."""
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo, tenant_id=tenant_id, name="rule-1", conditions=(_condition(),), action=_action()
    )
    attacker_tenant = uuid4()
    with pytest.raises(RuleNotFoundError):
        await cancel_rule(repo, tenant_id=attacker_tenant, rule_id=created.rule_id)


@pytest.mark.asyncio
async def test_cancel_rule_unknown_id_denied(tenant_id) -> None:
    repo = InMemoryAutomationRuleRepository()
    with pytest.raises(RuleNotFoundError):
        await cancel_rule(repo, tenant_id=tenant_id, rule_id=uuid4())


def test_empty_conditions_rejected(tenant_id) -> None:
    """규칙 스키마 자체가 조건 0개를 거부한다(negative test)."""
    from datetime import datetime, timezone
    from uuid import uuid4 as _uuid4

    with pytest.raises(ValidationError, match="최소 1개"):
        AutomationRule(
            rule_id=_uuid4(),
            tenant_id=tenant_id,
            name="empty",
            conditions=(),
            action=_action(),
            status=RuleStatus.ACTIVE,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
