from __future__ import annotations

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
P95_BUDGET_MS = P95_BUDGET_SECONDS * 1000  # 1000.0 ms — 내부 비교만 ms 단위
"""U-4 DoD(task-2631 spec): 규칙 100개 평가 p95 < 1s."""


@pytest.mark.perf
@pytest.mark.asyncio
async def test_evaluate_100_rules_p95_under_budget(tenant_id, perf_budget) -> None:
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

    samples = await perf_budget.samples_async(
        lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots=snapshots),
        n=SAMPLES,
    )
    triggered = samples[-1].result  # 마지막 샘플의 결과(동일 호출)
    durations = [s.cpu_ms for s in samples]

    assert len(triggered) == 50  # threshold 50..99 중 100보다 작은 것만 발동 = 50개
    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    assert p95 < P95_BUDGET_MS, f"p95={p95:.4f}ms exceeds {P95_BUDGET_MS:.1f}ms budget"
