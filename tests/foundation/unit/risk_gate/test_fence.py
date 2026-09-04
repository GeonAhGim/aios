"""domain/fence.py 순수 규칙 단위테스트 — DB 없음."""
from __future__ import annotations

from uuid import uuid4

from src.foundation.risk_gate.domain.fence import fence_pairs_for, is_stale
from src.foundation.risk_gate.domain.models import GLOBAL_SCOPE_REF, FenceSnapshot, SafetyScope


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
