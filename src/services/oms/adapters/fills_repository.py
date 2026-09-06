"""`fills` Postgres 어댑터(L4 명세 §9 L4-08).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-C, §5.1 fills 행
("`ON CONFLICT (venue, provider_fill_id) DO NOTHING`; 삽입된 경우에만
`filled_quantity` 재계산(fills 합산으로 — 누적값 신뢰 안 함)").

재계산은 두 문장이다 — 먼저 `orders` 행을 `FOR UPDATE`로 잠그고(다른 동시
삽입의 재계산과 순서를 정한다), 그다음 `fills` 합산으로 `filled_quantity`를
덮어쓴다. 한 문장으로 `UPDATE ... SET filled_quantity = (SELECT SUM...)`만
쓰면 READ COMMITTED에서 두 동시 트랜잭션이 서로 다른 스냅샷의 SUM을 계산한
뒤 잠금 해제 순서대로 마지막 쓰기가 이기는 "lost update"가 난다 — 먼저
행을 잠그면 두 번째 트랜잭션은 첫 번째가 커밋한 뒤에야 자기 SUM을 다시
계산하므로 항상 최신 합계를 쓴다. `orders`의 `oms_enforce_order_transition`
트리거(073beca589d5)는 `filled_quantity`만 바뀌는 UPDATE에서도 I3(비감소)·
I5(version+1)를 강제하지만, `status`가 그대로라 I2/I4/I6(전이표·이벤트
동반)는 건드리지 않는다.
"""
from __future__ import annotations

from uuid import UUID

import asyncpg

from src.services.oms.contracts.v1_events import FillEvent

_RECALC_LOCK_SQL = "SELECT 1 FROM orders WHERE order_id = $1 FOR UPDATE"
_RECALC_UPDATE_SQL = (
    "UPDATE orders SET filled_quantity = "
    "(SELECT COALESCE(SUM(quantity), 0) FROM fills WHERE order_id = $1) "
    "WHERE order_id = $1"
)


class FillsRepository:
    """`FillRepoPort` 구현체 — I/O 전부 이 클래스 안에만 있다."""

    async def insert_if_absent(self, conn: asyncpg.Connection, fill: FillEvent) -> bool:
        row = await conn.fetchval(
            "INSERT INTO fills ("
            "provider_fill_id, venue, order_id, exchange_order_id, symbol, side, "
            "quantity, price, fee, fee_currency, liquidity, venue_ts"
            ") VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12) "
            "ON CONFLICT (venue, provider_fill_id) DO NOTHING RETURNING id",
            fill.provider_fill_id,
            fill.venue,
            fill.order_id,
            fill.exchange_order_id,
            fill.symbol,
            fill.side,
            fill.quantity,
            fill.price,
            fill.fee,
            fill.fee_currency,
            fill.liquidity,
            fill.venue_ts,
        )
        inserted = row is not None
        if inserted and fill.order_id is not None:
            await conn.execute(_RECALC_LOCK_SQL, fill.order_id)
            await conn.execute(_RECALC_UPDATE_SQL, fill.order_id)
        return inserted

    async def list_for_order(
        self, conn: asyncpg.Connection, order_id: UUID
    ) -> list[FillEvent]:
        records = await conn.fetch(
            "SELECT provider_fill_id, venue, order_id, exchange_order_id, symbol, side, "
            "quantity, price, fee, fee_currency, liquidity, venue_ts "
            "FROM fills WHERE order_id = $1 ORDER BY venue_ts",
            order_id,
        )
        return [
            FillEvent(
                provider_fill_id=r["provider_fill_id"],
                venue=r["venue"],
                order_id=r["order_id"],
                exchange_order_id=r["exchange_order_id"],
                symbol=r["symbol"],
                side=r["side"],
                quantity=r["quantity"],
                price=r["price"],
                fee=r["fee"],
                fee_currency=r["fee_currency"],
                liquidity=r["liquidity"],
                venue_ts=r["venue_ts"],
            )
            for r in records
        ]
