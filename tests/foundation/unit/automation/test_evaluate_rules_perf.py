from __future__ import annotations

import time
from decimal import Decimal

import pytest

from src.foundation.automation.adapters.in_memory_repository import InMemoryAutomationRuleRepository
from src.foundation.automation.application.create_rule import create_rule
from src.foundation.automation.application.evaluate_rules import evaluate_active_rules
from src.foundation.automation.contracts.v1 import NotifyAction, PriceCondition
from src.foundation.automation.domain.evaluate import MarketSnapshot

from .conftest import make_candle

RULE_COUNT = 100
SAMPLES = 20
P95_BUDGET_SECONDS = 1.0
"""U-4 DoD(task-2631 spec): 규칙 100개 평가 p95 < 1s."""


@pytest.mark.asyncio
async def test_evaluate_100_rules_p95_under_budget(tenant_id) -> None:
    repo = InMemoryAutomationRuleRepository()
    for i in range(RULE_COUNT):
        condition = PriceCondition(symbol="005930", operator=">", threshold=Decimal(str(50 + i)))
        await create_rule(
            repo,
            tenant_id=tenant_id,
            name=f"rule-{i}",
            conditions=(condition,),
            action=NotifyAction(message_template="triggered"),
        )
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("100")))
    snapshots = {"005930": snapshot}

    durations: list[float] = []
    for _ in range(SAMPLES):
        start = time.perf_counter()
        triggered = await evaluate_active_rules(repo, tenant_id=tenant_id, snapshots=snapshots)
        durations.append(time.perf_counter() - start)

    assert len(triggered) == 50  # threshold 50..99 중 100보다 작은 것만 발동 = 50개
    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    assert p95 < P95_BUDGET_SECONDS, f"p95={p95:.4f}s exceeds {P95_BUDGET_SECONDS}s budget"
