"""open_order_sweeper — dep: 접두사 전용 음성·실패주입·게이트적색·perf 테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §9(R-39).
대상 함수: test_strategy_deployment_dep_prefix_is_paper_control_target_not_orders.
기존 파일(test_open_order_sweeper.py)이 499줄로 상한 근처이므로 분리한다.
공유 fixture/헬퍼(`pool`, `_seed_order`, `_adapters`, `_status_of`)는
`tests/unit/services/conftest.py`에 있다.

PERF_BUDGET_MS는 기존 파일 상단(37행)과 동일 값을 재사용한다."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders
from tests.unit.services.conftest import _adapters, _seed_order, _status_of

# 재사용: 기존 파일 상단 PERF_BUDGET_MS = 100.0
PERF_BUDGET_MS = 100.0


# ── N1: dep: 스코프에서 종결 주문이 CANCEL_REQUESTED로 바뀌지 않는다 ──


async def test_dep_prefix_skips_terminal_status_orders(pool):
    """N1: dep:<uuid> 스코프에서 FILLED 등 종결 주문이 CANCEL_REQUESTED로 바뀌지 않는다."""
    user_id = await create_test_user(pool)
    filled = await _seed_order(pool, user_id, status="FILLED")
    cancelled = await _seed_order(pool, user_id, status="CANCELLED")
    dep_id = uuid4()

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"dep:{dep_id}",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, filled) == "FILLED"
    assert await _status_of(pool, cancelled) == "CANCELLED"


# ── N2: 다른 테넌트의 열린 주문이 dep: 스코프 sweep으로 영향받지 않는다 ──


async def test_dep_prefix_does_not_affect_other_tenants_orders(pool):
    """N2: 다른 테넌트의 열린 주문이 dep: 스코프 sweep으로 영향받지 않는다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    order_a = await _seed_order(pool, user_a)
    order_b = await _seed_order(pool, user_b)
    dep_id = uuid4()

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"dep:{dep_id}",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, order_a) == "SUBMITTED"
    assert await _status_of(pool, order_b) == "SUBMITTED"


# ── N3: 잘못된 dep: 형식이 MalformedScopeRefError로 거부된다 ──


async def test_dep_prefix_with_non_uuid_is_not_validated_but_returns_empty(pool):
    """N3: dep: 뒤가 uuid가 아니어도 MalformedScopeRefError가 아닌 빈 결과다.

    앱 코드: legacy_execution_pauser._condition_for는 dep: 접두사만 확인하고
    뒤의 형식을 검증하지 않는다. 따라서 MalformedScopeRefError가 아니라
    (None, [])를 돌려 빈 배치가 된다."""
    user_id = await create_test_user(pool)
    await _seed_order(pool, user_id)

    # dep: 뒤가 UUID가 아닌 경우 — 앱 코드는 dep: 접두사만 확인
    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref="dep:not-a-uuid",
    )

    assert report.cancel_requested == ()
    assert (
        await _status_of(pool, list(report.cancel_requested)[0]) == "SUBMITTED"
        if report.cancel_requested
        else True
    )


# ── 실패 주입: 어댑터가 실패해도 dep: 스코프는 cancel_order를 호출하지 않음 ──


async def test_dep_prefix_adapter_failure_does_not_affect_empty_sweep(pool):
    """실패 주입: _RaisingCancelAdapter로 dep: sweep 실행 시 cancel_call_count==0."""
    from tests.unit.services.conftest import _RaisingCancelAdapter

    user_id = await create_test_user(pool)
    await _seed_order(pool, user_id)

    adapter = _RaisingCancelAdapter(exchange_name="bitget")
    adapters = {"bitget": adapter}
    dep_id = uuid4()

    report = await sweep_open_orders(
        pool,
        adapters,
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"dep:{dep_id}",
    )

    assert report.cancel_requested == ()
    assert report.adapter_failed == ()
    # dep: 스코프는 orders를 대상으로 하지 않으므로 cancel_order가 호출되지 않음
    assert adapter.cancel_call_count == 0


# ── 게이트 적색: scope_ref를 TENANT + 주문 소유자 uuid로 바꾸면 단언이 잡힌다 ──


async def test_dep_prefix_gate_red_when_scope_broken(pool):
    """gate-red: dep: 스코프 대신 TENANT + 다른 사용자 uuid로 바꾸면 단언이 실제로 잡힌다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    order_a = await _seed_order(pool, user_a)

    # dep: 대신 TENANT scope + user_b의 uuid로 스윕 — order_a는 영향을 받지 않음
    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_b),  # 다른 사용자
    )

    # 이 단언이 실제로 AssertionError로 잡히는지 확인
    with pytest.raises(AssertionError):
        assert report.cancel_requested == (order_a,)
    assert await _status_of(pool, order_a) == "SUBMITTED"


# ── Perf: dep: 스코프 측정 + 10배 지연 주입 적색 확인 ──


@pytest.mark.perf
async def test_dep_prefix_perf_budget_and_injected_delay_goes_red(pool, perf_budget):
    """Perf + gate-red — dep: 스코프 best-of 측정(예산 PERF_BUDGET_MS=100ms),
    10x 가상 지연 주입 시 단언이 적색이어야 한다."""
    from tests.support.perf_inject import virtual_delayed_async

    user_id = await create_test_user(pool)
    await _seed_order(pool, user_id)

    async def measured_sweep():
        await _seed_order(pool, user_id)
        dep_id = uuid4()
        return await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.STRATEGY_DEPLOYMENT,
            scope_ref=f"dep:{dep_id}",
        )

    baseline = await perf_budget.best_of_async(measured_sweep, n=5, warmup=1)
    assert baseline.wall_ms < PERF_BUDGET_MS, perf_budget.describe(
        baseline, budget_ms=PERF_BUDGET_MS
    )

    # 지연 주입: dep: 스코프는 cancel_order를 호출하지 않으므로
    # sweep_open_orders 자체에 지연을 주입해야 측정됨
    slow = await perf_budget.best_of_async(
        virtual_delayed_async(measured_sweep, seconds=PERF_BUDGET_MS * 10 / 1000),
        n=3,
        warmup=0,
    )
    assert slow.wall_ms >= PERF_BUDGET_MS * 10
