"""L4-15 보조 — inbox 처리로 새로 반영된 체결을 position_ledger에 적용.

`inbox_processor._process_row`가 새 체결을 삽입할 때(부분/종결 무관, task-7998/F3)
반환하는 `LedgerUpdate`를 받아, 커밋 이후 별도 커넥션에서 `position_ledger.
record_fill_in_position_ledger`를 정확히 1회 호출한다(§FD-4.2-c와 동일 컨벤션 —
ledger 반영은 order 트랜잭션과 원자적이지 않다, submit.py/apply_fill()도 항상
이 방식). 이 모듈이 `inbox_processor.py`에서 분리된 이유는 책임 축이 다르기
때문이다: 저쪽은 inbox 행 처리·FSM 전이, 이쪽은 그 결과를 ledger에 반영하는
후속 효과(P6.line_cap 300줄 상한 — task-8046).
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import NamedTuple
from uuid import UUID

import asyncpg

from src.core.observability.metrics import MetricsPort
from src.data.models.base import Money
from src.services.order_service import repository as legacy_order_repository
from src.services.order_service.position_ledger import record_fill_in_position_ledger

logger = logging.getLogger(__name__)


class LedgerUpdate(NamedTuple):
    """Payload for one newly applied fill (task-7998/F3) — this fill's own
    quantity/price, not the order's cumulative `filled_quantity`/
    `average_fill_price` (passing the cumulative value on every partial fill
    would double-count)."""

    order_id: UUID
    fill_quantity: Decimal
    fill_price: Money
    fill_seq: int


async def apply_position_ledger(
    pool: asyncpg.Pool, update: LedgerUpdate, *, metrics: MetricsPort
) -> None:
    async with pool.acquire() as conn:
        full_order = await legacy_order_repository.get_by_order_id(conn, update.order_id)
    if full_order is None:
        logger.warning(
            "inbox_processor: 체결 반영 뒤 order_id=%s 조회 실패 — position_ledger 생략",
            update.order_id,
        )
        return
    await record_fill_in_position_ledger(
        pool,
        full_order,
        fill_quantity=update.fill_quantity,
        fill_price=update.fill_price,
        fill_seq=update.fill_seq,
        metrics=metrics,
    )
