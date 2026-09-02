"""FD-8.2/8.3 — 체결(FILLED) 주문을 positions 테이블에 반영.

Spec: 04_db_schema_v1.7.md (Positions). PM 배정(agent-platform-12,
2026-09-02) — risk_guard_service.py/portfolio_service.py가 이미
positions를 LEFT JOIN해 PnL을 합산하지만, 이 테이블에 실제로 쓰는
경로가 지금까지 없어서 항상 0/NULL이었다(position.py 모듈 docstring이
이를 이미 인지: "positions 테이블에 아직 아무 서비스도 쓰지 않는다").

호출 지점 2곳 — 둘 다 "주문이 FILLED로 확정된 순간"이지만 서로 다른
경로다: `submit.py::apply_fill()`(FD-3.4 폴링으로 나중에 체결 확인)과
`executor.py::Executor.execute()`의 동기체결 분기(place_order가 즉시
FILLED를 반환한 경우, apply_fill을 거치지 않고 submit_order 안에서
바로 영속화됨). 두 경로를 하나로 합치면 submit_order()가 FSM/포지션
개념을 알아야 해 FD-4.2의 경계선(8.2-A)을 넘으므로, 로직은 이 파일
하나에 두고 호출부만 2곳에서 부른다.

Phase 1 가정(position.py와 동일): 실행당 종목 1개, 분할청산 없음
(전량청산만) — BUY는 항상 새 포지션을 열고, SELL은 항상 그 포지션
전체를 닫는다.
"""
from __future__ import annotations

import logging
from decimal import Decimal

import asyncpg

from src.data.models.trading import Order, OrderSide, OrderStatus

logger = logging.getLogger(__name__)


async def record_fill_in_position_ledger(pool: asyncpg.Pool, order: Order) -> None:
    """`order.status`가 FILLED가 아니면 아무것도 하지 않는다 — 호출부가
    이미 확인했더라도 방어적으로 한 번 더 checked."""
    if order.status != OrderStatus.FILLED:
        return
    if order.execution_id is None:
        # FD-8 실행 컨텍스트 없이 만들어진 주문(테스트/시뮬레이션 등) —
        # 포지션 원장 대상이 아니다.
        return

    if order.side == OrderSide.BUY:
        await _open_position(pool, order)
    else:
        await _close_position(pool, order)


def _fill_price(order: Order) -> Decimal:
    return order.average_fill_price.amount if order.average_fill_price is not None else Decimal("0")


async def _open_position(pool: asyncpg.Pool, order: Order) -> None:
    async with pool.acquire() as conn:
        user_id = await conn.fetchval(
            "SELECT user_id FROM strategy_executions WHERE id = $1", order.execution_id
        )
        if user_id is None:
            logger.warning(
                "position_ledger: execution_id=%s의 user_id를 찾을 수 없어 "
                "포지션을 열지 못했습니다.",
                order.execution_id,
            )
            return
        await conn.execute(
            """
            INSERT INTO positions (
                user_id, symbol, exchange, strategy_id, execution_id,
                quantity, average_entry_price, entry_time, asset_class
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, now(), $8)
            """,
            user_id,
            order.symbol,
            order.exchange,
            order.strategy_id,
            order.execution_id,
            order.filled_quantity,
            _fill_price(order),
            order.asset_class.value,
        )


async def _close_position(pool: asyncpg.Pool, order: Order) -> None:
    async with pool.acquire() as conn:
        open_position = await conn.fetchrow(
            """
            SELECT id, average_entry_price FROM positions
            WHERE execution_id = $1 AND closed_at IS NULL AND quantity <> 0
            ORDER BY entry_time DESC LIMIT 1
            """,
            order.execution_id,
        )
        if open_position is None:
            # Phase 1 가정(SELL은 항상 여는 포지션이 있음)이 깨진 상태 —
            # 조용히 realized_pnl을 유실하는 대신 로그를 남긴다. 이 함수의
            # 책임 밖(대사/reconciliation 대상)이라 예외로 fill 자체를
            # 실패시키지는 않는다.
            logger.warning(
                "position_ledger: execution_id=%s에 닫을 열린 포지션이 없습니다 "
                "(order_id=%s) — realized_pnl 기록 생략.",
                order.execution_id,
                order.order_id,
            )
            return
        realized_pnl = (_fill_price(order) - open_position["average_entry_price"]) * (
            order.filled_quantity
        )
        await conn.execute(
            """
            UPDATE positions SET quantity = 0, realized_pnl = realized_pnl + $2,
                closed_at = now(), updated_at = now()
            WHERE id = $1
            """,
            open_position["id"],
            realized_pnl,
        )
