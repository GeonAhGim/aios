"""FD-8.2/8.3 — 실행별 현재 보유 수량.

positions 테이블에 아직 아무 서비스도 쓰지 않는다(이 세션 이전까지 실제
매매 실행 루프 자체가 없었다) — 대신 이미 신뢰 원천인 orders 테이블
(FD-4.2-c)에서 체결된 주문만 집계해 파생한다. Phase 1은 실행당 종목 1개,
분할청산 없음(전량청산만)이라 이 파생만으로 충분하다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import asyncpg


async def compute_position_quantity(pool: asyncpg.Pool, execution_id: int) -> Decimal:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COALESCE(SUM(filled_quantity) FILTER (WHERE side = 'BUY'), 0) AS bought,
                COALESCE(SUM(filled_quantity) FILTER (WHERE side = 'SELL'), 0) AS sold
            FROM orders
            WHERE execution_id = $1 AND status = 'FILLED'
            """,
            execution_id,
        )
    return Decimal(row["bought"]) - Decimal(row["sold"])


async def compute_user_positions(
    pool: asyncpg.Pool, user_id: UUID, *, current_prices: dict[str, Decimal]
) -> list[tuple[str, Decimal]]:
    """FD-8.3 Correlation Risk 집계용 — 이 사용자의 모든 실행이 보유한
    포지션을 심볼별 시가평가액으로 합산한다. `current_prices`에 없는
    심볼은 마지막 체결가로 근사한다(Draft — 실시간 시세 조회 실패 시
    완전히 빠뜨리기보다 근사치가 낫다는 판단)."""
    async with pool.acquire() as conn:
        results = await conn.fetch(
            """
            SELECT symbol,
                COALESCE(SUM(filled_quantity) FILTER (WHERE side = 'BUY'), 0)
                    - COALESCE(SUM(filled_quantity) FILTER (WHERE side = 'SELL'), 0)
                    AS net_quantity,
                AVG(average_fill_price)
                    FILTER (WHERE average_fill_price IS NOT NULL) AS avg_price
            FROM orders o
            WHERE o.user_id = $1 AND o.status = 'FILLED'
            GROUP BY symbol
            """,
            user_id,
        )
    positions: list[tuple[str, Decimal]] = []
    for row in results:
        quantity = Decimal(row["net_quantity"])
        if quantity == 0:
            continue
        price = current_prices.get(row["symbol"]) or (
            Decimal(row["avg_price"]) if row["avg_price"] is not None else None
        )
        if price is None:
            continue
        positions.append((row["symbol"], quantity * price))
    return positions
