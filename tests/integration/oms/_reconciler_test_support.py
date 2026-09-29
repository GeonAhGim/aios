"""Shared fixtures/helpers for `test_three_way_reconciler*.py` (split at 500-LOC
policy, ADR-2026-09-10-C §7) -- both files import from here instead of
duplicating the DB/adapter scaffolding.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.base import AssetClass
from src.data.models.trading import Order as ProviderOrder
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


class ScriptedAdapter(FakeExchangeAdapter):
    """`get_open_orders()`만 스크립트로 제어한다 — 나머지는 공용 대역 그대로."""

    def __init__(self, *, open_orders: list[ProviderOrder] | None = None, fail: bool = False):
        super().__init__()
        self._scripted_open_orders = open_orders or []
        self._fail = fail

    async def get_open_orders(self, symbol: str | None = None) -> list[ProviderOrder]:
        if self._fail:
            raise TimeoutError("provider timed out (test double)")
        return self._scripted_open_orders


def provider_order(cid: str, quantity: Decimal, filled_quantity: Decimal) -> ProviderOrder:
    return ProviderOrder(
        exchange_order_id=f"ex-{uuid.uuid4().hex[:8]}",
        client_order_id=cid,
        strategy_id="s1",
        strategy_version="1.0.0",
        symbol="BTC/USDT",
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        status=OrderStatus.SUBMITTED,
        filled_quantity=filled_quantity,
        asset_class=AssetClass.CRYPTO,
    )


async def insert_open_order(
    pool,
    tenant_id: uuid.UUID,
    *,
    client_order_id: str,
    quantity: Decimal,
    filled_quantity: Decimal,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity
            ) VALUES ($1, $2, 'oms-recon-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', $3, 'SUBMITTED', $4)
            RETURNING order_id
            """,
            tenant_id,
            client_order_id,
            quantity,
            filled_quantity,
        )


async def insert_order_with_status(
    pool,
    tenant_id: uuid.UUID,
    *,
    client_order_id: str,
    quantity: Decimal,
    filled_quantity: Decimal,
    status: str,
) -> uuid.UUID:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO orders (
                user_id, client_order_id, strategy_id, strategy_version, symbol,
                exchange, side, order_type, quantity, status, filled_quantity
            ) VALUES ($1, $2, 'oms-recon-test', '1.0.0', 'BTC/USDT', 'bitget', 'BUY',
                      'LIMIT', $3, $5, $4)
            RETURNING order_id
            """,
            tenant_id,
            client_order_id,
            quantity,
            filled_quantity,
            status,
        )


async def record_cancel_requested(pool, order_id: uuid.UUID, status: str) -> None:
    """`cancel_order.py`가 남기는 자기루프 `CANCEL_REQUESTED` 이력을 흉내낸다
    (`order_events`에 직접 기록 — API를 거치지 않는 테스트 전용 단축 경로)."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO order_events (
                order_id, from_status, to_status, event, reason_code,
                actor_subject_id, trace_id, command_id, provider_event_id,
                occurred_at, payload_hash
            ) VALUES ($1, $2, $2, 'CANCEL_REQUESTED', 'user_requested', 'system',
                      $3, NULL, NULL, now(), $4)
            """,
            order_id,
            status,
            uuid.uuid4(),
            "a" * 64,
        )


async def order_status(pool, order_id: uuid.UUID) -> str:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)


async def active_account_controls(pool, tenant_id: uuid.UUID) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT count(*) FROM safety_control WHERE scope = 'ACCOUNT' AND scope_ref = $1 "
            "AND state = 'ACTIVE'",
            str(tenant_id),
        )


def profile() -> VenueCapabilityProfile:
    return VenueCapabilityProfile(
        venue="bitget",
        asset_classes=[AssetClass.CRYPTO],
        order_types={OrderType.MARKET, OrderType.LIMIT},
        time_in_force={"GTC", "IOC"},
        supports_client_order_id=True,
        client_order_id_max_len=40,
        client_order_id_charset="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        id_policy="STABLE",
        supports_modify=True,
        supports_cancel="YES",
        supports_ws_orders=True,
        supports_batch=False,
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
        submit_timeout=TimeoutBudget(),
        query_timeout=TimeoutBudget(),
        market_hours=None,
        max_open_orders_per_symbol=20,
        verified="DOC_ONLY",
    )


def registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT",
        "bitget",
        "BTCUSDT",
        tick=Decimal("0.1"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


async def create_running_execution(pool, user_id: uuid.UUID) -> int:
    strategy_id = f"oms-recon-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 100, 'USDT', 'RUNNING')
            RETURNING id
            """,
            strategy_id,
            user_id,
        )
    return row["id"]


def submit_cmd(user_id: uuid.UUID, execution_id: int) -> SubmitOrderCommand:
    scope = OrderIdempotencyScope(
        tenant_id=user_id,
        account_ref="acct-1",
        provider="bitget",
        strategy_id="s1",
        strategy_version="1.0.0",
        execution_id=execution_id,
        intent_seq=1,
        window_start=datetime.now(timezone.utc),
    )
    return SubmitOrderCommand(
        command_id=uuid.uuid4(),
        trace_id=uuid.uuid4(),
        scope=scope,
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=user_id,
        issued_at=datetime.now(timezone.utc),
    )
