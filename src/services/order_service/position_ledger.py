"""FD-8.2/8.3 — 체결(FILLED) 주문을 positions 원장에 반영.

LB-12: 계산은 B 도메인 record_fill(LB-11)에 위임(원가법·부분청산은
LB-2/LB-3 selector가 SSOT, 재구현 금지). pos_account/pos_snapshot 부트
스트랩 → record_fill → legacy positions 투영, 3단계 얇은 어댑터. 미검증:
동시 첫 체결 경합 시 pos_account 중복 생성 가능(connection_id NULL은
UNIQUE 제약이 구분 못함) — Phase 1과 동일하게 막지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg

from src.data.models.base import Currency, Money
from src.data.models.trading import Order, OrderStatus
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import record_fill
from src.foundation.positions.contracts.v1 import (
    CostMethod,
    PositionSnapshotView,
    RecordFillCommand,
)
from src.foundation.positions.domain.position_key import PositionKey

logger = logging.getLogger(__name__)


async def record_fill_in_position_ledger(pool: asyncpg.Pool, order: Order) -> None:
    """FILLED가 아니거나 실행 컨텍스트가 없으면 아무것도 하지 않는다."""
    if order.status != OrderStatus.FILLED or order.execution_id is None:
        return
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT user_id FROM strategy_executions WHERE id = $1", order.execution_id
        )
        if user_id is None:
            logger.warning("position_ledger: execution_id=%s user_id 없음", order.execution_id)
            return
        position_key = str(PositionKey(
            venue=order.exchange, instrument_id=order.symbol, strategy_id=order.strategy_id,
            execution_id=str(order.execution_id),
        ))
        fill_price = order.average_fill_price
        currency = fill_price.currency if fill_price else Currency.USDT
        price = fill_price.amount if fill_price else Decimal("0")
        async with conn.transaction():
            snapshots = PostgresSnapshotRepository(pool)
            existing = await snapshots.get(conn, user_id, position_key)
            if existing is not None:
                account_id = existing.account_id
            else:
                account_id = await conn.fetchval(
                    "SELECT account_id FROM pos_account WHERE tenant_id = $1 AND venue = $2 "
                    "AND connection_id IS NULL",
                    user_id, order.exchange,
                )
                if account_id is None:
                    account_id = await conn.fetchval(
                        "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
                        "VALUES ($1, $2, $3, $4) RETURNING account_id",
                        user_id, order.exchange, currency.value, CostMethod.FIFO.value,
                    )
                empty = PositionSnapshotView(
                    position_key=position_key, tenant_id=user_id, account_id=account_id,
                    instrument_id=uuid4(), quantity=Decimal("0"), cost_method=CostMethod.FIFO,
                    avg_cost=Money(amount=Decimal("0"), currency=currency), lots=[],
                    realized_pnl_base=Decimal("0"), unrealized_pnl_base=None,
                    fees_base=Decimal("0"), funding_base=Decimal("0"),
                    mark_price=None, mark_at=None, base_currency=currency,
                    last_journal_seq=0, updated_at=datetime.now(timezone.utc),
                )
                await snapshots.upsert(conn, empty, expected_seq=0)
            command = RecordFillCommand(
                tenant_id=user_id, account_id=account_id, position_key=position_key,
                order_id=order.order_id, fill_seq=1, side=order.side,
                quantity=order.filled_quantity, price=Money(amount=price, currency=currency),
                fee=None, occurred_at=order.updated_at, trace_id=uuid4(),
            )
            snapshot = await record_fill(
                conn, command, asset_class=order.asset_class, snapshots=snapshots,
                journal=PostgresJournalRepository(pool), audit=PostgresAuditEventRepository(pool),
                clock=lambda: datetime.now(timezone.utc),
            )
            legacy_id = await conn.fetchval(
                "SELECT legacy_position_id FROM pos_snapshot WHERE position_key = $1", position_key
            )
            closed_at = None if snapshot.quantity != 0 else datetime.now(timezone.utc)
            if legacy_id is None:
                legacy_id = await conn.fetchval(
                    "INSERT INTO positions (user_id, symbol, exchange, strategy_id, "
                    "execution_id, quantity, average_entry_price, realized_pnl, entry_time, "
                    "closed_at, asset_class) VALUES "
                    "($1,$2,$3,$4,$5,$6,$7,$8,now(),$9,$10) RETURNING id",
                    user_id, order.symbol, order.exchange, order.strategy_id,
                    order.execution_id, snapshot.quantity, snapshot.avg_cost.amount,
                    snapshot.realized_pnl_base, closed_at, order.asset_class.value,
                )
                await conn.execute(
                    "UPDATE pos_snapshot SET legacy_position_id = $1 WHERE position_key = $2",
                    legacy_id, position_key,
                )
            else:
                await conn.execute(
                    "UPDATE positions SET quantity = $2, average_entry_price = $3, "
                    "realized_pnl = $4, closed_at = $5, updated_at = now() WHERE id = $1",
                    legacy_id, snapshot.quantity, snapshot.avg_cost.amount,
                    snapshot.realized_pnl_base, closed_at,
                )
