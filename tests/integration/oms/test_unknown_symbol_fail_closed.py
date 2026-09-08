"""L4-04 DoD (b) — 미등록 심볼은 실 TEST_DATABASE_URL 경로에서도 fail-closed다.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-04.

반증 입력: venue=bitget, canonical="ZZZUSDT"(production 레지스트리에 등록되지
않음)로 `submit_order`를 호출하면 `UnknownSymbolError`로 거부되고, orders/
order_command_outbox 어느 쪽에도 행이 남지 않는다 — 그 결과 `OutboxDispatcher`가
그 뒤에 한 틱을 돌려도 처리할 outbox 행이 없으므로 어댑터 spy 호출 횟수는
정확히 0이다(로그만 남기고 통과하면 이 마지막 단언이 실패한다).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.application.submit_order import submit_order
from src.services.oms.application.wiring import (
    build_outbox_dispatcher,
    build_production_symbol_registry,
)
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import UnknownSymbolError
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.oms.conftest import create_test_tenant, seed_entity_context
from tests.support.oms_outbox_fakes import ScriptedAdapter


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(outcome=GateOutcome.ALLOW)


async def _drain_stale_outbox(pool) -> None:
    """공유 테스트 DB에는 트랜잭션 격리가 없어(모듈 docstring), 먼저 끝난
    다른 oms 테스트가 처리하지 않은 `order_command_outbox` PENDING 행을 남길
    수 있다. 이 테스트의 핵심 단언(`report.claimed == 0`)은 우리 실행과 무관한
    잔여 행 수에 흔들리면 안 되므로, 우리 자신의 spy로 재는 직전에 별도
    어댑터로 큐를 먼저 비운다(무한루프 방지로 상한을 둔다)."""

    async def _resolve(tenant_id: uuid.UUID, exchange: str) -> FakeExchangeAdapter:
        return FakeExchangeAdapter()

    drainer = build_outbox_dispatcher(
        pool,
        resolve_adapter=_resolve,
        outbox_repo=OutboxRepository(),
        order_repo=PostgresOrderRepository(),
        worker_id=f"drain-stale-outbox-{uuid.uuid4().hex[:8]}",
    )
    for _ in range(20):
        report = await drainer.dispatch_once()
        if report.claimed == 0:
            return


async def _create_running_execution(pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-unknown-symbol-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id, user_id, json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id, user_id,
        )
    return row["id"]


async def test_unknown_symbol_rejected_before_any_row_and_adapter_never_called(pool) -> None:
    await _drain_stale_outbox(pool)
    user_id = await create_test_tenant(pool)
    execution_id = await _create_running_execution(pool, user_id)
    entity_context = await seed_entity_context(pool, user_id)
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    cmd = SubmitOrderCommand(
        command_id=uuid.uuid4(), trace_id=uuid.uuid4(), scope=scope,
        symbol="ZZZUSDT", side=OrderSide.BUY, order_type=OrderType.MARKET,
        quantity=Decimal("0.01"), asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id, issued_at=datetime.now(timezone.utc),
    )

    with pytest.raises(UnknownSymbolError):
        await submit_order(
            cmd,
            pool=pool,
            profile=BITGET_SPOT_PROFILE,
            registry=build_production_symbol_registry(),
            pre_submit_gate=_allow_gate,
            entity_context=entity_context,
            entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE execution_id = $1", execution_id
        )
        outbox_count = await conn.fetchval(
            "SELECT count(*) FROM order_command_outbox oco JOIN orders o "
            "ON o.order_id = oco.order_id WHERE o.execution_id = $1",
            execution_id,
        )
    assert order_count == 0
    assert outbox_count == 0

    spy = ScriptedAdapter()

    async def _resolve_adapter(tenant_id: uuid.UUID, exchange: str) -> ScriptedAdapter:
        return spy

    dispatcher = build_outbox_dispatcher(
        pool,
        resolve_adapter=_resolve_adapter,
        outbox_repo=OutboxRepository(),
        order_repo=PostgresOrderRepository(),
        worker_id=f"fail-closed-test-{uuid.uuid4().hex[:8]}",
    )
    report = await dispatcher.dispatch_once()

    assert report.claimed == 0
    assert spy.calls == []
