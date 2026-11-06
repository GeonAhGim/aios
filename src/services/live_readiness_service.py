"""모의 운용 -> 실전 전환 근거 조회 서비스 (J8 LP-2).

PAPER 실행 하나의 주문 행을 읽어 `compute_live_readiness`(LP-1)에 넘긴다.
소유자·모드 검사는 `convert_to_live`와 같은 규칙을 쓴다.

수수료는 주문 번호(`client_order_id`)로 `paper_sim_orders`에서 가져온다
(`orders`에는 수수료 열이 없다). 같은 주문 번호가 여러 계좌에 있어도 행이 늘지 않도록
먼저 주문 번호별로 합친다.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from src.services.execution_types import ExecutionControlError
from src.services.live_readiness import LiveReadiness, compute_live_readiness

_ORDER_ROWS_SQL = """
SELECT o.status, o.side, o.quantity, o.filled_quantity, o.price, o.average_fill_price,
       o.created_at, o.updated_at, p.fee_total, p.fee_currency
FROM orders o
LEFT JOIN (
    SELECT client_order_id, SUM(fee_total) AS fee_total, MIN(fee_currency) AS fee_currency
    FROM paper_sim_orders
    GROUP BY client_order_id
) p ON p.client_order_id = o.client_order_id
WHERE o.execution_id = $1
ORDER BY o.created_at
"""


class LiveReadinessService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def for_execution(self, execution_id: int, user_id: UUID) -> LiveReadiness:
        async with self._pool.acquire() as conn:
            execution = await conn.fetchrow(
                "SELECT user_id, mode FROM strategy_executions WHERE id = $1", execution_id
            )
            if execution is None:
                raise ExecutionControlError("존재하지 않는 실행입니다.")
            if execution["user_id"] != user_id:
                raise ExecutionControlError("본인의 실행만 조회할 수 있습니다.")
            if execution["mode"] != "PAPER":
                raise ExecutionControlError("PAPER 모드 실행만 전환 근거를 조회할 수 있습니다.")
            records = await conn.fetch(_ORDER_ROWS_SQL, execution_id)
        rows: list[dict[str, Any]] = [dict(r) for r in records]
        return compute_live_readiness(rows)
