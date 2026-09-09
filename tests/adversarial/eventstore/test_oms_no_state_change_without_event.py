"""FA-16 — adversarial: OMS submit/cancel/modify never change `orders`
without a matching `order_events` row, in the same transaction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16
(선행 FA-14=task-2050, FA-15=task-2060). FA-14가 정한 이벤트 원천을 그대로
쓴다 — 새 이벤트 테이블은 없다: OMS의 이벤트 원천은 `order_events`이고,
`order_repository.transition()`이 세 경로(submit/cancel/modify) 모두가
공유하는 유일한 전이 지점이다(I-10 모듈 docstring). "이벤트 append가
상태 변경보다 먼저, 같은 `conn`, 같은 트랜잭션"은 이미 구현돼 있다 —
`073beca589d5`의 `oms_enforce_order_transition_trg`가 cutover 무장 후
DB 레벨에서 이 순서를 강제한다(`tests/integration/oms/
test_db_transition_trigger.py::test_status_change_without_order_event_
raises_once_cutover_armed`가 그 트리거 자체를 이미 검증). 이 파일은
cutover 여부와 무관하게 실제 애플리케이션 경로(submit_order/cancel_order/
modify_order)를 호출해 그 배선이 살아 있음을 증명한다.

DoD(1) "배선증명 없이 통과하는 테스트는 반려" — `test_oms_submit_cancel_
modify_each_produce_exactly_one_order_event`는 `order_events` 행 수를
`orders.version`과 직접 비교하므로, `order_repository.transition()`의
`await self._events.append(conn, event)` 줄을 지우면 다음 줄
(`event.model_copy(update={"seq": seq})`)이 `NameError`로 즉시 죽어
이 테스트가 FAIL한다 — 실제로 그 줄을 임시로 주석 처리하고 이 테스트가
FAIL하는 것을 확인한 뒤 원복했다(회귀 방지를 위해 sabotage 코드는
커밋에 남기지 않는다).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.services.oms.adapters.order_events_repository import PostgresOrderEventRepository
from src.services.oms.application import cancel_order as cancel_order_module
from src.services.oms.application.cancel_order import cancel_order
from src.services.oms.application.modify_order import modify_order
from src.services.oms.application.submit_order import submit_order
from src.services.oms.contracts.v1_commands import (
    CancelOrderCommand,
    ModifyOrderCommand,
    OrderIdempotencyScope,
    SubmitOrderCommand,
)
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from src.services.order_service.gate import GateDecision, GateOutcome, OrderContext
from tests.integration.conftest import create_test_tenant
from tests.integration.oms.conftest import insert_order, seed_entity_context


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile.model_validate(defaults)


def _registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT", "bitget", "BTCUSDT",
        tick=Decimal("0.1"), lot=Decimal("0.0001"), min_notional=Decimal("5"), quote_ccy="USDT",
    )
    return reg


async def _allow_gate(context: OrderContext) -> GateDecision:
    return GateDecision(
        outcome=GateOutcome.ALLOW, decision_id=uuid4(), compliance_decision_id=uuid4()
    )


async def _create_running_execution(pool: asyncpg.Pool, user_id: UUID) -> int:
    strategy_id = f"fa16-adv-{uuid4().hex[:8]}"
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


async def _submit_command(pool: asyncpg.Pool, user_id: UUID) -> SubmitOrderCommand:
    execution_id = await _create_running_execution(pool, user_id)
    scope = OrderIdempotencyScope(
        tenant_id=user_id, account_ref="acct-1", provider="bitget", strategy_id="s1",
        strategy_version="1.0.0", execution_id=execution_id, intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid4(), trace_id=uuid4(), scope=scope, symbol="BTC/USDT",
        side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO, actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )


async def _order_state(pool: asyncpg.Pool, order_id: UUID) -> tuple[str, int]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, version FROM orders WHERE order_id = $1", order_id
        )
        event_count = await conn.fetchval(
            "SELECT count(*) FROM order_events WHERE order_id = $1", order_id
        )
    if row is None:
        return "MISSING", 0
    assert event_count == row["version"], (
        f"order {order_id}: version={row['version']} but order_events count={event_count} "
        "— state changed without a matching event (FA-16 I-10 violation)."
    )
    return row["status"], row["version"]


async def test_oms_submit_cancel_modify_each_produce_exactly_one_order_event(pool):
    """Positive — DoD(1). §9 FA-16 대상 세 경로 각각 정확히 1개의
    `order_events` 행을 남기고, `orders.version`이 그 개수와 정확히
    일치한다(0건의 "이벤트 없는 상태 변경")."""
    user_id = await create_test_tenant(pool)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = await _submit_command(pool, user_id)
    submitted = await submit_order(
        cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
        entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
    )
    status, version = await _order_state(pool, submitted.order_id)
    assert status == "VALIDATED" and version == 1

    async with pool.acquire() as conn:
        cancel_target = await insert_order(conn, user_id, status="ACKNOWLEDGED")
    cancelled = await cancel_order(
        CancelOrderCommand(
            command_id=uuid4(), trace_id=uuid4(), order_id=cancel_target, tenant_id=user_id,
            reason="fa16-adv-test", actor_subject_id=user_id, issued_at=datetime.now(timezone.utc),
        ),
        pool=pool,
    )
    _, version = await _order_state(pool, cancelled.order_id)
    assert version == 1

    async with pool.acquire() as conn:
        modify_target = await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, price, status, filled_quantity
            ) VALUES ($1, $2, 'fa16-adv-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', 1, 100, 'ACKNOWLEDGED', 0)
            RETURNING order_id
            """,
            user_id, f"fa16-adv-modify-{uuid4().hex}",
        )
    modified = await modify_order(
        ModifyOrderCommand(
            command_id=uuid4(), trace_id=uuid4(), order_id=modify_target, tenant_id=user_id,
            reason="fa16-adv-test", actor_subject_id=user_id, issued_at=datetime.now(timezone.utc),
            new_price=Decimal("101"),
        ),
        pool=pool, profile=_profile(),
    )
    _, version = await _order_state(pool, modified.order_id)
    assert version == 1


async def test_oms_event_append_failure_blocks_order_state_change(pool, monkeypatch):
    """Negative — DoD(3) fail-closed. `order_events` append 자체가 실패하면
    `orders` UPDATE는 이 함수까지 도달조차 못 한다(순서상 append가 먼저,
    §5.1) — tx 전체가 롤백돼 0행."""
    async def _boom(self, conn, ev):  # noqa: ANN001, ARG001 -- 테스트 전용 fault injection
        raise RuntimeError("injected order_events append failure")

    monkeypatch.setattr(PostgresOrderEventRepository, "append", _boom)

    user_id = await create_test_tenant(pool)
    entity_context = await seed_entity_context(pool, user_id)
    cmd = await _submit_command(pool, user_id)

    with pytest.raises(RuntimeError, match="injected order_events append failure"):
        await submit_order(
            cmd, pool=pool, profile=_profile(), registry=_registry(), pre_submit_gate=_allow_gate,
            entity_context=entity_context, entity_repo=PostgresEntityRepository(pool),
        )

    async with pool.acquire() as conn:
        order_count = await conn.fetchval(
            "SELECT count(*) FROM orders WHERE user_id = $1", user_id
        )
    assert order_count == 0


async def test_oms_transaction_rollback_removes_event_and_state_together(pool, monkeypatch):
    """DoD(2) — 실DB. `_orders.transition()`(order_events insert + orders
    UPDATE)이 성공한 *뒤* 같은 트랜잭션의 다른 단계(outbox enqueue)가
    실패하면, 커밋되지 않은 order_events 행도 orders 변경분과 함께 사라진다
    (같은 트랜잭션이라는 것의 실제 증거 — 부분 커밋 없음)."""
    async def _boom(conn, **kwargs):  # noqa: ANN001, ARG001 -- 인스턴스 속성 패치라 self 없음
        raise RuntimeError("injected outbox enqueue failure")

    monkeypatch.setattr(cancel_order_module._outbox, "enqueue", _boom)

    user_id = await create_test_tenant(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="ACKNOWLEDGED")

    with pytest.raises(RuntimeError, match="injected outbox enqueue failure"):
        await cancel_order(
            CancelOrderCommand(
                command_id=uuid4(), trace_id=uuid4(), order_id=order_id, tenant_id=user_id,
                reason="fa16-adv-rollback", actor_subject_id=user_id,
                issued_at=datetime.now(timezone.utc),
            ),
            pool=pool,
        )

    status, version = await _order_state(pool, order_id)
    assert status == "ACKNOWLEDGED" and version == 0
