"""open_order_sweeper 통합테스트 — SafetyScope 매핑 및 scope_ref 검증.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번), §9(R-39).
FA-16(task-2406): docs/specs/ibor_fund_accounting_and_resilience.md#§9.
5개 SafetyScope 매핑(GLOBAL/PROVIDER/TENANT/ACCOUNT/STRATEGY_DEPLOYMENT)과
scope_ref 형식 위반 거부(negative test)를 실제 Postgres 행으로 검증한다.

동시성·멱등성·어댑터 부분 실패는 `test_open_order_sweeper_concurrency.py`,
order_events 원자성·수치 성능 단언은 `test_open_order_sweeper_edge.py`로
분리했다(task-10872, CLAUDE.md ADR-2026-09-10-C LOC 규율). 공유 fixture/헬퍼
(`pool`, `_seed_order`, `_seed_execution`, `_status_of`, `_adapters`)는
`conftest.py`에 있다.

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md #2406)가 이 리프의 D3 하한
미달로 지적한 공백 중 negative·실패주입·게이트재현·D3증명은 이 분할 전체와
`tests/adversarial/eventstore/test_no_state_change_without_event.py`가 이미
채웠다(task-2432, 7f82784c/1e4f73b4/51cb0bef)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import (
    MalformedScopeRefError,
    UnmappedSafetyScopeError,
)
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders
from tests.unit.services.conftest import (
    _adapters,
    _RaisingCancelAdapter,
    _seed_execution,
    _seed_order,
    _status_of,
)


async def test_global_scope_requests_cancel_across_tenants(pool):
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    order_a = await _seed_order(pool, user_a, exchange="bitget")
    order_b = await _seed_order(pool, user_b, exchange="binance")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget", "binance"),
        control_id=uuid4(),
        scope=SafetyScope.GLOBAL,
        scope_ref="",
    )

    assert set(report.cancel_requested) >= {order_a, order_b}
    # GLOBAL scope는 조건이 TRUE라 공유 테스트 DB의 다른 테스트/이전 실행이
    # 남긴 미매핑 거래소 주문까지 함께 쓸어간다(재현 확인됨) — 이 리프가
    # 통제하지 못하는 전역 상태를 단언하지 않고, 이 테스트가 만든 두 주문만
    # 실패하지 않았는지 확인한다.
    assert not {order_a, order_b} & set(report.adapter_failed)
    assert await _status_of(pool, order_a) == "CANCEL_REQUESTED"
    assert await _status_of(pool, order_b) == "CANCEL_REQUESTED"


async def test_provider_scope_only_matching_exchange(pool):
    user_id = await create_test_user(pool)
    bitget_order = await _seed_order(pool, user_id, exchange="bitget")
    binance_order = await _seed_order(pool, user_id, exchange="binance")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget", "binance"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
    )

    assert report.cancel_requested == (bitget_order,)
    assert await _status_of(pool, binance_order) == "SUBMITTED"


async def test_tenant_scope_does_not_affect_other_tenants_order(pool):
    """negative test — 타 테넌트 주문 미영향(DoD 필수 항목)."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    order_a = await _seed_order(pool, tenant_a)
    order_b = await _seed_order(pool, tenant_b)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(tenant_a),
    )

    assert report.cancel_requested == (order_a,)
    assert await _status_of(pool, order_b) == "SUBMITTED"


async def test_account_scope_only_that_accounts_order(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    order_a = await _seed_order(pool, tenant_a)
    order_b = await _seed_order(pool, tenant_b)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_a),
    )

    assert report.cancel_requested == (order_a,)
    assert await _status_of(pool, order_b) == "SUBMITTED"


async def test_strategy_deployment_exec_prefix_only_that_execution(pool):
    user_id = await create_test_user(pool)
    target_exec = await _seed_execution(pool, user_id)
    other_exec = await _seed_execution(pool, user_id)
    target_order = await _seed_order(pool, user_id, execution_id=target_exec)
    other_order = await _seed_order(pool, user_id, execution_id=other_exec)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"exec:{target_exec}",
    )

    assert report.cancel_requested == (target_order,)
    assert await _status_of(pool, other_order) == "SUBMITTED"


async def test_strategy_deployment_dep_prefix_is_paper_control_target_not_orders(pool):
    """§3.8: STRATEGY_DEPLOYMENT의 dep:<uuid>는 paper_control 전용 —
    `orders`에는 대응 컬럼이 없으므로 0건이 정답이다."""
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.STRATEGY_DEPLOYMENT,
        scope_ref=f"dep:{uuid4()}",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, order_id) == "SUBMITTED"


async def test_terminal_status_orders_are_skipped_not_raised(pool):
    user_id = await create_test_user(pool)
    filled = await _seed_order(pool, user_id, status="FILLED")
    cancelled = await _seed_order(pool, user_id, status="CANCELLED")
    rejected = await _seed_order(pool, user_id, status="REJECTED")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert report.cancel_requested == ()
    assert set(report.skipped) == {filled, cancelled, rejected}
    assert await _status_of(pool, filled) == "FILLED"
    assert await _status_of(pool, cancelled) == "CANCELLED"
    assert await _status_of(pool, rejected) == "REJECTED"


async def test_unmapped_scope_raises_instead_of_silently_matching_zero_rows(pool):
    with pytest.raises(UnmappedSafetyScopeError):
        await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope="BOGUS_SCOPE",  # type: ignore[arg-type]
            scope_ref="irrelevant",
        )


async def test_tenant_scope_rejects_non_uuid_scope_ref(pool):
    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.TENANT,
            scope_ref="not-a-uuid",
        )


async def test_strategy_deployment_exec_prefix_rejects_non_digit_id(pool):
    """negative test(task-4139 DEEPEN) — `exec:` 접두사가 있어도 뒤가 정수가
    아니면 §3.8 'exec:<int>' 형식 위반으로 거부한다(조용히 0건이 아니라
    fail-closed)."""
    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.STRATEGY_DEPLOYMENT,
            scope_ref="exec:not-a-number",
        )


# ---- DEEPEN-DEPTH R-39 (task-23314): test_provider_scope_only_matching_exchange 보강 ----


async def test_provider_scope_unknown_provider_cancels_nothing(pool):
    """negative — 어떤 주문의 거래소와도 맞지 않는 scope_ref는 0건, 주문 불변."""
    user_id = await create_test_user(pool)
    order = await _seed_order(pool, user_id, exchange="bitget")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref=f"no-such-{uuid4().hex}",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, order) == "SUBMITTED"


async def test_provider_scope_is_case_sensitive_exact_match(pool):
    """negative — 'BITGET' 대문자 scope_ref가 소문자 'bitget' 주문을 쓸어가면 안 된다."""
    user_id = await create_test_user(pool)
    order = await _seed_order(pool, user_id, exchange="bitget")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="BITGET",
    )

    assert order not in report.cancel_requested
    assert await _status_of(pool, order) == "SUBMITTED"


async def test_provider_scope_terminal_orders_of_matching_exchange_untouched(pool):
    """negative — 일치 거래소라도 종결 상태 주문은 취소 요청 대신 skipped."""
    user_id = await create_test_user(pool)
    filled = await _seed_order(pool, user_id, exchange="bitget", status="FILLED")
    cancelled = await _seed_order(pool, user_id, exchange="bitget", status="CANCELLED")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
    )

    assert not {filled, cancelled} & set(report.cancel_requested)
    assert await _status_of(pool, filled) == "FILLED"
    assert await _status_of(pool, cancelled) == "CANCELLED"


async def test_provider_scope_adapter_exception_recorded_and_other_exchange_untouched(pool):
    """failure injection — 일치 거래소 어댑터 cancel이 예외를 던져도 스윕은 완주하고
    adapter_failed에 기록되며(주문은 CANCEL_REQUESTED 유지), 타 거래소 주문은 불변."""
    user_id = await create_test_user(pool)
    bitget_order = await _seed_order(pool, user_id, exchange="bitget")
    binance_order = await _seed_order(pool, user_id, exchange="binance")
    adapters = _adapters("binance")
    adapters["bitget"] = _RaisingCancelAdapter(exchange_name="bitget")

    report = await sweep_open_orders(
        pool,
        adapters,
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
    )

    assert report.cancel_requested == (bitget_order,)
    assert report.adapter_failed == (bitget_order,)
    assert await _status_of(pool, bitget_order) == "CANCEL_REQUESTED"
    assert await _status_of(pool, binance_order) == "SUBMITTED"


# 신설 예산(기존 예산 아님): PROVIDER 스윕 1회(주문 시드 + sweep) CPU 예산.
# 로컬 실측(2026-11-11) best_of(n=5, batch=4): cpu_ms=0.0(15.6ms 틱 양자화로 0 판독),
# wall_ms≈2.5. 예산 100ms는 wall 실측의 40배(30배 이상 여유)이며 cpu·wall 양쪽에 적용한다.
_PROVIDER_SWEEP_CPU_BUDGET_MS = 100.0


async def _seed_and_sweep_provider(pool, adapters):
    user_id = await create_test_user(pool)
    await _seed_order(pool, user_id, exchange="bitget")
    return await sweep_open_orders(
        pool,
        adapters,
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
    )


@pytest.mark.perf
async def test_provider_scope_sweep_best_of_cpu_within_budget(pool, perf_budget):
    """성능 단언 — best_of(여러 번 중 최소) CPU가 신설 예산 미만."""
    sample = await perf_budget.best_of_async(
        lambda: _seed_and_sweep_provider(pool, _adapters("bitget")), n=5, warmup=1, batch=4
    )
    assert sample.cpu_ms < _PROVIDER_SWEEP_CPU_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PROVIDER_SWEEP_CPU_BUDGET_MS
    )
    assert sample.wall_ms < _PROVIDER_SWEEP_CPU_BUDGET_MS, perf_budget.describe(
        sample, budget_ms=_PROVIDER_SWEEP_CPU_BUDGET_MS
    )


@pytest.mark.perf
async def test_provider_scope_sweep_gate_red_with_virtual_delay_10x(pool, perf_budget):
    """게이트 적색 재현 — 예산의 10배 가상 지연을 주입하면 같은 단언이 실제로 적색이 된다."""
    reset_virtual_totals()
    try:
        slow = virtual_delayed_async(
            lambda: _seed_and_sweep_provider(pool, _adapters("bitget")),
            _PROVIDER_SWEEP_CPU_BUDGET_MS * 10 / 1000,
        )
        sample = await perf_budget.best_of_async(slow, n=3, warmup=0, batch=2)
    finally:
        reset_virtual_totals()

    assert sample.wall_ms >= _PROVIDER_SWEEP_CPU_BUDGET_MS * 10
    with pytest.raises(AssertionError):
        assert sample.wall_ms < _PROVIDER_SWEEP_CPU_BUDGET_MS, perf_budget.describe(
            sample, budget_ms=_PROVIDER_SWEEP_CPU_BUDGET_MS
        )
