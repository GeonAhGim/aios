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


async def test_provider_scope_unknown_exchange_matches_nothing_and_leaves_orders(pool):
    """negative(R-39 DEEPEN) — 어떤 주문도 갖지 않은 거래소를 지목하면 0건이고
    같은 테넌트의 기존 주문은 전부 그대로여야 한다(과다 취소 방지)."""
    user_id = await create_test_user(pool)
    bitget_order = await _seed_order(pool, user_id, exchange="bitget")
    binance_order = await _seed_order(pool, user_id, exchange="binance")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget", "binance"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref=f"no-such-exchange-{uuid4().hex[:8]}",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, bitget_order) == "SUBMITTED"
    assert await _status_of(pool, binance_order) == "SUBMITTED"


async def test_provider_scope_ref_is_case_sensitive_exact_match(pool):
    """negative — 'BITGET'는 'bitget'의 별칭이 아니다. 대소문자 불일치는
    조용히 전부를 취소하거나 부분 일치하지 않고 0건(fail-closed)이다."""
    user_id = await create_test_user(pool)
    bitget_order = await _seed_order(pool, user_id, exchange="bitget")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="BITGET",
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, bitget_order) == "SUBMITTED"


async def test_provider_scope_skips_terminal_orders_of_matching_exchange(pool):
    """negative — 매칭 거래소라도 종결 상태 주문은 취소 요청 대상이 아니라
    skipped로만 보고되고 상태가 바뀌지 않는다."""
    user_id = await create_test_user(pool)
    live = await _seed_order(pool, user_id, exchange="bitget")
    filled = await _seed_order(pool, user_id, exchange="bitget", status="FILLED")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="bitget",
    )

    assert live in report.cancel_requested
    assert filled not in report.cancel_requested
    assert filled in report.skipped
    assert await _status_of(pool, filled) == "FILLED"


async def test_provider_scope_adapter_failure_does_not_leak_to_other_exchange(pool):
    """실패주입 — 매칭 거래소 어댑터의 cancel이 예외를 던져도 스윕은 완주하고
    주문은 adapter_failed로 기록되며, 다른 거래소 주문은 건드리지 않는다."""
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

    assert report.adapter_failed == (bitget_order,)
    assert await _status_of(pool, binance_order) == "SUBMITTED"


async def test_provider_scope_assertion_catches_broken_scope_ref(pool):
    """게이트 적색 재현 — 입력(scope_ref)을 망가뜨려 'binance'로 바꾸면
    test_provider_scope_only_matching_exchange의 단언이 실제로 적색이 된다."""
    user_id = await create_test_user(pool)
    bitget_order = await _seed_order(pool, user_id, exchange="bitget")
    await _seed_order(pool, user_id, exchange="binance")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget", "binance"),
        control_id=uuid4(),
        scope=SafetyScope.PROVIDER,
        scope_ref="binance",
    )

    with pytest.raises(AssertionError):
        assert report.cancel_requested == (bitget_order,)
    assert await _status_of(pool, bitget_order) == "SUBMITTED"


# 실측(로컬 Windows, PG, best-of-3 wall): seed+sweep 1건 ≈ 2.2 ms.
# 예산 100ms는 실측의 45배 여유(CI 경합 흡수), 예산 10배 가상 지연으로 적색 검출.
_PROVIDER_SWEEP_BUDGET_MS = 100.0


@pytest.mark.perf
async def test_provider_scope_sweep_wall_within_budget_and_injected_delay_trips_it(
    pool, perf_budget
):
    """성능 단언 + 검출력 — 정상 호출은 예산 이내, 예산의 10배에 해당하는
    가상 지연(perf_inject)을 주입하면 같은 단언이 적색이어야 한다."""
    from tests.support.perf_inject import virtual_delayed_async

    user_id = await create_test_user(pool)

    async def _seed_and_sweep():
        await _seed_order(pool, user_id, exchange="bitget")
        return await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.PROVIDER,
            scope_ref="bitget",
        )

    sample = await perf_budget.best_of_async(_seed_and_sweep, n=3, warmup=1)
    assert sample.wall_ms < _PROVIDER_SWEEP_BUDGET_MS, sample.wall_ms

    delayed = virtual_delayed_async(_seed_and_sweep, seconds=_PROVIDER_SWEEP_BUDGET_MS * 10 / 1000)
    slow = await perf_budget.best_of_async(delayed, n=3)
    assert slow.wall_ms >= _PROVIDER_SWEEP_BUDGET_MS


async def test_account_scope_rejects_non_uuid_scope_ref(pool):
    """negative — ACCOUNT scope_ref가 UUID가 아니면 fail-closed로 거부한다."""
    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.ACCOUNT,
            scope_ref="not-a-uuid",
        )


async def test_account_scope_unknown_account_matches_nothing_and_leaves_orders(pool):
    """negative — 주문이 없는 계정을 지목하면 0건이고 다른 계정 주문은 그대로다."""
    tenant = await create_test_user(pool)
    order = await _seed_order(pool, tenant)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(uuid4()),
    )

    assert report.cancel_requested == ()
    assert await _status_of(pool, order) == "SUBMITTED"


async def test_account_scope_skips_terminal_orders_of_that_account(pool):
    """negative — 해당 계정의 종결 상태 주문은 skipped로만 보고되고 불변이다."""
    tenant = await create_test_user(pool)
    live = await _seed_order(pool, tenant)
    filled = await _seed_order(pool, tenant, status="FILLED")

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant),
    )

    assert live in report.cancel_requested
    assert filled in report.skipped
    assert await _status_of(pool, filled) == "FILLED"


async def test_account_scope_adapter_failure_does_not_leak_to_other_account(pool):
    """실패주입 — 어댑터 cancel 예외는 adapter_failed로 기록되고 타 계정 주문은 불변."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    order_a = await _seed_order(pool, tenant_a)
    order_b = await _seed_order(pool, tenant_b)

    report = await sweep_open_orders(
        pool,
        {"bitget": _RaisingCancelAdapter(exchange_name="bitget")},
        control_id=uuid4(),
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_a),
    )

    assert report.adapter_failed == (order_a,)
    assert await _status_of(pool, order_b) == "SUBMITTED"


async def test_account_scope_assertion_catches_broken_scope_ref(pool):
    """게이트 적색 재현 — scope_ref를 타 계정으로 망가뜨리면
    test_account_scope_only_that_accounts_order의 단언이 실제로 적색이 된다."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    order_a = await _seed_order(pool, tenant_a)
    await _seed_order(pool, tenant_b)

    report = await sweep_open_orders(
        pool,
        _adapters("bitget"),
        control_id=uuid4(),
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_b),
    )

    with pytest.raises(AssertionError):
        assert report.cancel_requested == (order_a,)
    assert await _status_of(pool, order_a) == "SUBMITTED"


# 실측(로컬 Windows, PG, best-of-3 wall): seed+sweep 1건 ≈ 2.5 ms(PROVIDER와 동급).
# 예산 100ms는 실측의 40배 여유(CI 경합 흡수), 예산 10배 가상 지연으로 적색 검출.
_ACCOUNT_SWEEP_BUDGET_MS = 100.0


@pytest.mark.perf
async def test_account_scope_sweep_wall_within_budget_and_injected_delay_trips_it(
    pool, perf_budget
):
    """성능 단언 + 검출력 — 정상 호출은 예산 이내, 10배 가상 지연은 적색."""
    from tests.support.perf_inject import virtual_delayed_async

    tenant = await create_test_user(pool)

    async def _seed_and_sweep():
        await _seed_order(pool, tenant)
        return await sweep_open_orders(
            pool,
            _adapters("bitget"),
            control_id=uuid4(),
            scope=SafetyScope.ACCOUNT,
            scope_ref=str(tenant),
        )

    sample = await perf_budget.best_of_async(_seed_and_sweep, n=3, warmup=1)
    assert sample.wall_ms < _ACCOUNT_SWEEP_BUDGET_MS, sample.wall_ms

    delayed = virtual_delayed_async(_seed_and_sweep, seconds=_ACCOUNT_SWEEP_BUDGET_MS * 10 / 1000)
    slow = await perf_budget.best_of_async(delayed, n=3)
    assert slow.wall_ms >= _ACCOUNT_SWEEP_BUDGET_MS
