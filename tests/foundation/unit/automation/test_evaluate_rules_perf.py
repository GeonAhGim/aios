from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.automation.adapters.in_memory_repository import InMemoryAutomationRuleRepository
from src.foundation.automation.application.create_rule import create_rule
from src.foundation.automation.application.evaluate_rules import (
    evaluate_active_rules,
)
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


# ── Negative tests (DoD: ≥3) ────────────────────────────────────────────────


@pytest.mark.perf
@pytest.mark.asyncio
async def test_evaluate_zero_rules_returns_empty_quickly(tenant_id, perf_budget) -> None:
    """음성: 활성화된 규칙이 없으면 즉시 빈 튜플 반환 — CPU 측정 0 수렴."""
    repo = InMemoryAutomationRuleRepository()
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("100")))
    samples = await perf_budget.samples_async(
        lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots={"005930": snapshot}),
        n=SAMPLES,
    )
    for s in samples:
        assert s.result == ()
    durations = [s.cpu_ms for s in samples]
    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    # 0개 규칙: 내부 루프가 돌지 않으므로 CPU 시간 ≈ 0 (양자화 오차 범위 내)
    assert p95 < 50.0, f"zero-rule p95={p95:.4f}ms should be near-zero"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_evaluate_no_matching_rules_returns_empty(tenant_id, perf_budget) -> None:
    """음성: 모든 임계값이 현재 가격보다 높으면 아무 규칙도 발동하지 않음."""
    repo = InMemoryAutomationRuleRepository()
    for i in range(10):
        condition = PriceCondition(symbol="005930", operator=">", threshold=Decimal(str(200 + i)))
        await create_rule(
            repo,
            tenant_id=tenant_id,
            name=f"rule-{i}",
            conditions=(condition,),
            action=NotifyAction(message_template="triggered"),
        )
    snapshot = MarketSnapshot(candle=make_candle(close=Decimal("100")))
    samples = await perf_budget.samples_async(
        lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots={"005930": snapshot}),
        n=SAMPLES,
    )
    for s in samples:
        assert s.result == ()
    durations = [s.cpu_ms for s in samples]
    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    assert p95 < P95_BUDGET_MS, f"no-match p95={p95:.4f}ms exceeds budget"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_evaluate_empty_snapshots_returns_empty(tenant_id, perf_budget) -> None:
    """음성: 스냅샷이 비어 있으면 어떤 규칙도 조건을 평가할 데이터가 없어 fail-closed."""
    repo = InMemoryAutomationRuleRepository()
    for i in range(5):
        condition = PriceCondition(symbol="005930", operator=">", threshold=Decimal(str(50 + i)))
        await create_rule(
            repo,
            tenant_id=tenant_id,
            name=f"rule-{i}",
            conditions=(condition,),
            action=NotifyAction(message_template="triggered"),
        )
    # 빈 스냅샷 — symbol "005930"가 없음
    samples = await perf_budget.samples_async(
        lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots={}),
        n=SAMPLES,
    )
    for s in samples:
        assert s.result == ()
    durations = [s.cpu_ms for s in samples]
    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    assert p95 < P95_BUDGET_MS, f"empty-snapshot p95={p95:.4f}ms exceeds budget"


# ── Delay injection — budget assertion actually fires (gate-red repro) ──────


@pytest.mark.perf
@pytest.mark.asyncio
async def test_delay_injection_proves_budget_fails(tenant_id, perf_budget, monkeypatch) -> None:
    """적색 재현: evaluate_rules 내부에 지연을 주입하면 p95 단언이 AssertionError를
    일으킨다 — 즉 테스트가 진짜로 예산 위반을 잡아내는지를 검증한다.

    delayed_async(asyncio.sleep)는 wall-clock 대기를 주입한다.
    sample_async는 wall_ms를 측정하므로 wall_ms로 단언한다.
    """
    from tests.support.perf_inject import delayed_async

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

    # list_by_tenant을 지연시켜 전체 호출 시간을 예산(1초)보다 길게 만든다.
    # 1.1초 지연 → p95 wall-clock이 1000ms를 넘어야 한다.
    monkeypatch.setattr(
        repo,
        "list_by_tenant",
        delayed_async(repo.list_by_tenant, 1.1),
    )

    wall_durations = []
    for _ in range(SAMPLES):
        sample = await perf_budget.sample_async(
            lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots=snapshots)
        )
        wall_durations.append(sample.wall_ms)

    wall_durations.sort()
    p95_index = min(len(wall_durations) - 1, int(len(wall_durations) * 0.95))
    p95_wall = wall_durations[p95_index]
    # 지연 주입으로 wall-clock p95가 예산을 초과해야 한다.
    with pytest.raises(AssertionError):
        assert p95_wall < P95_BUDGET_MS, (
            f"p95_wall={p95_wall:.4f}ms should exceed {P95_BUDGET_MS:.1f}ms with injected delay"
        )


# ── CPU 부하 주입 — delayed_sync로 실제 CPU 소모 유도 ────────────────────────


@pytest.mark.perf
@pytest.mark.asyncio
async def test_cpu_load_injection_proves_budget_fails(tenant_id, perf_budget, monkeypatch) -> None:
    """적색 재현 2: delayed_sync로 CPU 부하를 주입하면 p95가 예산을 초과한다.
    evaluate_rules.application의 evaluate_rule import를 delayed_sync로
    감싸서 실제 CPU 소모를 유도한다.
    """
    from tests.support.perf_inject import delayed_sync

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

    # evaluate_rules.application.evaluate_rule에 monkeypatch — 해당 모듈이
    # domain.evaluate.evaluate_rule을 import한 참조를 덮어쓴다.
    from src.foundation.automation.application import evaluate_rules as eval_app_mod

    original_evaluate_rule = eval_app_mod.evaluate_rule
    # 15ms CPU 부하 per rule 호출 → 100개 규칙 × 15ms = 1500ms 총 부하
    monkeypatch.setattr(
        eval_app_mod,
        "evaluate_rule",
        delayed_sync(original_evaluate_rule, 0.015),
    )

    durations = []
    for _ in range(SAMPLES):
        sample = await perf_budget.sample_async(
            lambda: evaluate_active_rules(repo, tenant_id=tenant_id, snapshots=snapshots)
        )
        durations.append(sample.cpu_ms)

    durations.sort()
    p95_index = min(len(durations) - 1, int(len(durations) * 0.95))
    p95 = durations[p95_index]
    # 15ms × 100개 규칙 = 1500ms 부하 → p95가 예산(1000ms)을 초과해야 함
    with pytest.raises(AssertionError):
        assert p95 < P95_BUDGET_MS, f"p95={p95:.4f}ms should exceed budget with CPU load"
