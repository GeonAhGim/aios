"""open_order_sweeper 통합테스트 — 실제 TEST_DATABASE_URL 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8, §5(105번), §9(R-39).
5개 SafetyScope 매핑, 단일 조건부 UPDATE...RETURNING, 멱등성, 취소 불가
주문 skip 보고, 어댑터 부분 실패를 실제 Postgres 행으로 검증한다."""
from __future__ import annotations

import os
import uuid
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.exchanges.common.adapter import ExchangeAdapter
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import (
    MalformedScopeRefError,
    UnmappedSafetyScopeError,
)
from src.services.safety.open_order_sweeper import sweep_open_orders
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


class _RaisingCancelAdapter(FakeExchangeAdapter):
    """cancel_order가 항상 예외를 던지는 대역 — 어댑터 부분 실패 테스트 전용."""

    def __init__(self, *, exchange_name: str = "bitget") -> None:
        super().__init__(exchange_name=exchange_name)
        self.cancel_call_count = 0

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_call_count += 1
        raise RuntimeError("exchange unreachable")


class _CountingCancelAdapter(FakeExchangeAdapter):
    """cancel_order 호출 횟수를 세는 대역 — 멱등성 테스트 전용."""

    def __init__(self, *, exchange_name: str = "bitget") -> None:
        super().__init__(exchange_name=exchange_name)
        self.cancel_call_count = 0

    async def cancel_order(self, order_id: str) -> bool:
        self.cancel_call_count += 1
        return True


async def _seed_order(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    exchange: str = "bitget",
    status: str = "SUBMITTED",
    execution_id: int | None = None,
) -> UUID:
    exchange_order_id = f"ex-{uuid.uuid4().hex[:12]}" if status != "CREATED" else None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO orders (
                user_id, client_order_id, exchange_order_id, strategy_id,
                strategy_version, execution_id, symbol, exchange, side, order_type,
                quantity, status
            ) VALUES (
                $1, $2, $3, 'sweeper-test', '1.0.0', $4, 'BTC/USDT', $5, 'BUY',
                'LIMIT', 1.0, $6
            )
            RETURNING order_id
            """,
            user_id,
            f"sweeper-{uuid.uuid4().hex}",
            exchange_order_id,
            execution_id,
            exchange,
            status,
        )
    return row["order_id"]


async def _seed_execution(pool: asyncpg.Pool, user_id: UUID, *, exchange: str = "bitget") -> int:
    strategy_id = f"sweeper-exec-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
        )
    return row["id"]


async def _status_of(pool: asyncpg.Pool, order_id: UUID) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT status FROM orders WHERE order_id = $1", order_id)
    assert row is not None
    return row["status"]


def _adapters(*names: str) -> dict[str, ExchangeAdapter]:
    return {name: FakeExchangeAdapter(exchange_name=name) for name in names}


async def test_global_scope_requests_cancel_across_tenants(pool):
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    order_a = await _seed_order(pool, user_a, exchange="bitget")
    order_b = await _seed_order(pool, user_b, exchange="binance")

    report = await sweep_open_orders(
        pool, _adapters("bitget", "binance"), control_id=uuid4(), scope=SafetyScope.GLOBAL,
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
        pool, _adapters("bitget", "binance"), control_id=uuid4(), scope=SafetyScope.PROVIDER,
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
        pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.TENANT,
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
        pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.ACCOUNT,
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
        pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.STRATEGY_DEPLOYMENT,
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
        pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.STRATEGY_DEPLOYMENT,
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
        pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert report.cancel_requested == ()
    assert set(report.skipped) == {filled, cancelled, rejected}
    assert await _status_of(pool, filled) == "FILLED"
    assert await _status_of(pool, cancelled) == "CANCELLED"
    assert await _status_of(pool, rejected) == "REJECTED"


async def test_repeated_call_is_idempotent_and_does_not_recall_adapter(pool):
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id)
    adapter = _CountingCancelAdapter()
    control_id = uuid4()

    first = await sweep_open_orders(
        pool, {"bitget": adapter}, control_id=control_id, scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )
    second = await sweep_open_orders(
        pool, {"bitget": adapter}, control_id=control_id, scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert first.cancel_requested == (order_id,)
    assert second.cancel_requested == ()
    assert adapter.cancel_call_count == 1
    assert await _status_of(pool, order_id) == "CANCEL_REQUESTED"


async def test_adapter_failure_on_one_order_does_not_abort_sweep(pool):
    user_id = await create_test_user(pool)
    failing_order = await _seed_order(pool, user_id, exchange="bitget")
    ok_order = await _seed_order(pool, user_id, exchange="binance")
    failing_adapter = _RaisingCancelAdapter(exchange_name="bitget")

    report = await sweep_open_orders(
        pool,
        {"bitget": failing_adapter, "binance": FakeExchangeAdapter(exchange_name="binance")},
        control_id=uuid4(),
        scope=SafetyScope.TENANT,
        scope_ref=str(user_id),
    )

    assert set(report.cancel_requested) == {failing_order, ok_order}
    assert report.adapter_failed == (failing_order,)
    # 실패해도 상태를 되돌리지 않는다 — 결과는 reconcile에 위임(DoD 3).
    assert await _status_of(pool, failing_order) == "CANCEL_REQUESTED"
    assert await _status_of(pool, ok_order) == "CANCEL_REQUESTED"
    assert failing_adapter.cancel_call_count == 1


async def test_missing_adapter_for_exchange_is_reported_as_failed_not_raised(pool):
    user_id = await create_test_user(pool)
    order_id = await _seed_order(pool, user_id, exchange="bitget")

    report = await sweep_open_orders(
        pool, {}, control_id=uuid4(), scope=SafetyScope.TENANT, scope_ref=str(user_id)
    )

    assert report.cancel_requested == (order_id,)
    assert report.adapter_failed == (order_id,)
    assert await _status_of(pool, order_id) == "CANCEL_REQUESTED"


async def test_unmapped_scope_raises_instead_of_silently_matching_zero_rows(pool):
    with pytest.raises(UnmappedSafetyScopeError):
        await sweep_open_orders(
            pool, _adapters("bitget"), control_id=uuid4(), scope="BOGUS_SCOPE",  # type: ignore[arg-type]
            scope_ref="irrelevant",
        )


async def test_tenant_scope_rejects_non_uuid_scope_ref(pool):
    with pytest.raises(MalformedScopeRefError):
        await sweep_open_orders(
            pool, _adapters("bitget"), control_id=uuid4(), scope=SafetyScope.TENANT,
            scope_ref="not-a-uuid",
        )
