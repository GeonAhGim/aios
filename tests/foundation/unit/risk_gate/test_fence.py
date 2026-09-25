"""domain/fence.py 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.fence import fence_pairs_for, is_stale
from src.foundation.risk_gate.domain.models import GLOBAL_SCOPE_REF, FenceSnapshot, SafetyScope
from tests.conftest import PerfBudget


def test_fence_pairs_for_returns_the_five_fixed_pairs():
    tenant_id = uuid4()
    pairs = fence_pairs_for(tenant_id, "binance", "exec-1")

    assert pairs == (
        (SafetyScope.GLOBAL, GLOBAL_SCOPE_REF),
        (SafetyScope.PROVIDER, "binance"),
        (SafetyScope.TENANT, str(tenant_id)),
        (SafetyScope.ACCOUNT, str(tenant_id)),
        (SafetyScope.STRATEGY_DEPLOYMENT, "exec-1"),
    )
    assert len(pairs) == 5


def test_is_stale_false_when_tokens_are_identical():
    pair = (SafetyScope.ACCOUNT, "acct-1")
    snapshot = FenceSnapshot(tokens={pair: 3})

    assert is_stale(snapshot, snapshot) is False


def test_is_stale_true_when_any_token_increased():
    pair_a = (SafetyScope.ACCOUNT, "acct-1")
    pair_b = (SafetyScope.PROVIDER, "binance")
    observed = FenceSnapshot(tokens={pair_a: 3, pair_b: 1})
    current = FenceSnapshot(tokens={pair_a: 3, pair_b: 2})

    assert is_stale(observed, current) is True


def test_is_stale_true_when_a_pair_absent_from_observed_now_has_a_token():
    """관측 시점엔 activate된 적 없어 행 자체가 없던 pair — 기본값 0에서
    증가한 것으로 취급해야 stale이다(누락을 "변화 없음"으로 착각하면
    안 된다)."""
    pair = (SafetyScope.GLOBAL, GLOBAL_SCOPE_REF)
    observed = FenceSnapshot(tokens={})
    current = FenceSnapshot(tokens={pair: 1})

    assert is_stale(observed, current) is True


def test_is_stale_false_when_current_has_no_pairs():
    observed = FenceSnapshot(tokens={(SafetyScope.ACCOUNT, "acct-1"): 5})
    current = FenceSnapshot(tokens={})

    assert is_stale(observed, current) is False


def test_is_stale_raises_typeerror_instead_of_treating_corrupt_token_as_fresh() -> None:
    """오염된(비-int) 토큰 값은 `current_token > observed.tokens.get(pair,
    0)` 비교 자체가 예외로 시끄럽게 실패해야 한다(fail-closed). 조용히
    형변환되거나 False로 취급되면 오염된 fence-token 행이 "stale 아님"으로
    잘못 통과한다."""
    pair = (SafetyScope.ACCOUNT, "acct-1")
    observed = FenceSnapshot(tokens={pair: 0})
    current = FenceSnapshot(tokens={pair: cast(int, "not-a-number")})

    with pytest.raises(TypeError):
        is_stale(observed, current)


@pytest.mark.perf
def test_is_stale_meets_latency_budget_over_a_large_snapshot(perf_budget: PerfBudget) -> None:
    """1만 쌍 스냅샷 위에서 1000회 반복 호출이 절대시간 예산 내에 있어야
    한다(선형 스캔이 이차 이상으로 퇴화하면 fence 비교가 제출 경로 지연
    SLA를 못 지킨다)."""
    n = 10_000
    budget_ms = 2000.0
    observed_tokens = {(SafetyScope.ACCOUNT, str(i)): i for i in range(n)}
    current_tokens = dict(observed_tokens)
    bumped_pair = (SafetyScope.ACCOUNT, "0")
    current_tokens[bumped_pair] = current_tokens[bumped_pair] + 1
    observed = FenceSnapshot(tokens=observed_tokens)
    current = FenceSnapshot(tokens=current_tokens)

    def _run() -> None:
        for _ in range(1000):
            is_stale(observed, current)

    perf_budget.assert_within(
        _run,
        budget_ms=budget_ms,
        label=f"is_stale() x1000 over {n}-pair snapshot",
    )
