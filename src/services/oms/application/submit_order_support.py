"""300-line cap split of `submit_order.py` (L4-09) — the outbox payload builder,
event hash, and post-UNIQUE-collision lookup. All three are pure supporting
logic that runs outside the single tx (or before it's entered), same split
principle as `outbox_dispatcher.py` -> `outbox_writes.py` and
`unknown_resolver.py` -> `unknown_resolver_writes.py` (see those modules'
docstrings).

This file never issues an `orders` INSERT — EM-3
(`scripts/check_child_order_path.py`)'s invariant ("an INSERT INTO orders that
sets a child-identity column is only allowed inside submit_order.py") anchors
on that single file, so the INSERT SQL and its commit/rollback tx body stay
there and are excluded from this split.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

import asyncpg

from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_commands import SubmitOrderCommand
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.errors import IdempotencyDigestMismatchError


def venue_order(cmd: SubmitOrderCommand, *, order_id: UUID, client_id: str, venue: str) -> Order:
    """Outbox SUBMIT payload (§2-C "order" key) — `order_from_payload` only checks
    order_id/client_order_id agreement, so the remaining fields just need to be
    real values for the adapter call. Currency is fixed to USDT per the Phase 1
    convention (same deviation as `order_service/repository.py`)."""
    price = Money(amount=cmd.price, currency=Currency.USDT) if cmd.price is not None else None
    return Order(
        order_id=order_id,
        client_order_id=client_id,
        strategy_id=cmd.scope.strategy_id,
        strategy_version=cmd.scope.strategy_version,
        execution_id=cmd.scope.execution_id,
        symbol=cmd.symbol,
        exchange=venue,
        side=cmd.side,
        order_type=cmd.order_type,
        quantity=cmd.quantity,
        price=price,
        status=OrderStatus.VALIDATED,
        asset_class=cmd.asset_class,
        is_liquidation=cmd.is_liquidation,
    )


def event_payload_hash(order_id: UUID, scope_hash_val: str) -> str:
    canonical = json.dumps(
        {"order_id": str(order_id), "scope_hash": scope_hash_val}, sort_keys=True
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


async def resolve_after_collision(
    pool: asyncpg.Pool,
    *,
    order_repo: PostgresOrderRepository,
    scope_hash_val: str,
    digest: str,
) -> OrderView:
    """Called after an `orders.client_order_id` UNIQUE collision (the loser) —
    the winner's tx has already committed, so this looks it up in a fresh tx.
    Also compares digest and rejects fail-closed if the winner was a different
    command. `order_repo` is passed through as the caller's (`submit_order.py`)
    module singleton on purpose — failure-injection tests monkeypatch that
    singleton instance, so this function must not construct its own."""
    async with pool.acquire() as conn:
        stored_digest = await conn.fetchval(
            "SELECT digest FROM order_idempotency WHERE scope_hash = $1", scope_hash_val
        )
        if stored_digest is not None and stored_digest != digest:
            raise IdempotencyDigestMismatchError(scope_hash_val)
        existing = await order_repo.find_by_scope_hash(conn, scope_hash_val)
    if existing is None:
        raise RuntimeError(
            f"orders.client_order_id UNIQUE 충돌 뒤 scope_hash={scope_hash_val} 조회 실패 "
            "— 승자 tx가 아직 안 보입니다(격리수준/타이밍 가정 위반)."
        )
    return existing
