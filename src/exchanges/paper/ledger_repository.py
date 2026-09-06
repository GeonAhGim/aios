"""paper_sim 잔고·주문 원장의 asyncpg CRUD(L4 명세 §2-F §5.1, §9 L4-23).

순수 CRUD만 한다 — 체결가·수수료·지연 계산은 `fill_model`/`fee_model`/
`latency_model`(L4-22)이, 적용 순서는 `simulator_adapter`가 결정한다.
재시작 후 잔고가 보존돼야 하므로(DoD) 상태를 메모리에 캐시하지 않는다 —
모든 조회는 매번 실제 쿼리다. 잔고 변경은 105번 §2.1 조건부 UPDATE
(`WHERE available >= $amount`)로 fail-closed. `paper_sim_orders` 갱신은
073beca589d5 `orders.version` 관례를 재사용한 낙관적 락(CAS)이다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.exceptions import MihwaError
from src.data.models.trading import AccountBalance


class InsufficientBalanceError(MihwaError):
    """잔고 부족 — 조건부 UPDATE가 0행을 반환했다(스펙 §5.1 "음수 잔고 차단")."""

    def __init__(self, account_id: UUID, asset: str, amount: Decimal) -> None:
        super().__init__(f"account={account_id} asset={asset}: {amount} 차감에 가용잔고 부족.")


class PaperOrderNotFoundError(MihwaError):
    """`order_id`가 이 계정의 `paper_sim_orders`에 없다."""


@dataclass(frozen=True)
class PaperOrderRow:
    order_id: UUID
    account_id: UUID
    client_order_id: str
    symbol: str
    side: str
    order_type: str
    quantity: Decimal
    price: Decimal | None
    status: str
    filled_quantity: Decimal
    average_fill_price: Decimal | None
    fee_total: Decimal
    fee_currency: str | None
    fills: list[dict[str, object]]
    version: int
    created_at: datetime
    updated_at: datetime


def _row_to_order(row: asyncpg.Record) -> PaperOrderRow:
    """`PaperOrderRow` 필드명이 `paper_sim_orders` 컬럼명과 1:1이라 `dict(row)`를
    그대로 펼친다 — `fills`(JSONB)만 디코드해 덮어쓴다."""
    data = dict(row)
    fills = data["fills"]
    data["fills"] = json.loads(fills) if isinstance(fills, str) else list(fills)
    return PaperOrderRow(**data)


class PaperLedgerRepository:
    """상태 없음 — 매 호출이 넘겨받은 `conn`만으로 동작한다(재시작 생존 DoD)."""

    async def list_balances(
        self, conn: asyncpg.Connection, account_id: UUID, asset: str | None = None
    ) -> list[AccountBalance]:
        if asset is None:
            rows = await conn.fetch(
                "SELECT * FROM paper_sim_accounts WHERE account_id = $1 ORDER BY asset",
                account_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM paper_sim_accounts WHERE account_id = $1 AND asset = $2",
                account_id,
                asset,
            )
        return [
            AccountBalance(
                exchange="paper_sim",
                asset=row["asset"],
                total=row["total"],
                available=row["available"],
                used_margin=row["used_margin"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    async def get_balance(
        self, conn: asyncpg.Connection, account_id: UUID, asset: str
    ) -> AccountBalance | None:
        balances = await self.list_balances(conn, account_id, asset)
        return balances[0] if balances else None

    async def deposit(
        self, conn: asyncpg.Connection, account_id: UUID, asset: str, amount: Decimal
    ) -> None:
        """총액·가용액 동시 증액(입금·매도대금 수령·매수체결 수취 공용)."""
        if amount <= 0:
            raise ValueError("deposit: amount는 양수여야 합니다.")
        await conn.execute(
            "INSERT INTO paper_sim_accounts (account_id, asset, total, available) "
            "VALUES ($1, $2, $3, $3) ON CONFLICT (account_id, asset) DO UPDATE "
            "SET total = paper_sim_accounts.total + EXCLUDED.total, "
            "available = paper_sim_accounts.available + EXCLUDED.available, updated_at = now()",
            account_id,
            asset,
            amount,
        )

    async def debit(
        self, conn: asyncpg.Connection, account_id: UUID, asset: str, amount: Decimal
    ) -> None:
        """총액·가용액 동시 차감. 가용잔고 부족이면 fail-closed(§5.1)."""
        if amount <= 0:
            raise ValueError("debit: amount는 양수여야 합니다.")
        row = await conn.fetchrow(
            "UPDATE paper_sim_accounts SET available = available - $3, total = total - $3, "
            "updated_at = now() WHERE account_id = $1 AND asset = $2 AND available >= $3 "
            "RETURNING account_id",
            account_id,
            asset,
            amount,
        )
        if row is None:
            raise InsufficientBalanceError(account_id, asset, amount)

    async def insert_order(
        self,
        conn: asyncpg.Connection,
        *,
        account_id: UUID,
        client_order_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        price: Decimal | None,
    ) -> tuple[PaperOrderRow, bool]:
        """(account_id, client_order_id) UNIQUE 제약으로 멱등 — 재전송이면
        기존 행을 그대로 돌려주고 `created=False`(DoD 5: 원장 1행)."""
        row = await conn.fetchrow(
            "INSERT INTO paper_sim_orders (order_id, account_id, client_order_id, symbol, "
            "side, order_type, quantity, price) VALUES ($1, $2, $3, $4, $5, $6, $7, $8) "
            "ON CONFLICT (account_id, client_order_id) DO NOTHING RETURNING *",
            uuid4(),
            account_id,
            client_order_id,
            symbol,
            side,
            order_type,
            quantity,
            price,
        )
        if row is not None:
            return _row_to_order(row), True
        existing = await self.find_by_client_id(conn, account_id, client_order_id)
        if existing is None:  # ON CONFLICT를 쳤으니 반드시 존재해야 한다(방어적)
            raise PaperOrderNotFoundError(
                f"account_id={account_id} client_order_id={client_order_id}: "
                "ON CONFLICT 직후 재조회 실패"
            )
        return existing, False

    async def get_order(
        self, conn: asyncpg.Connection, account_id: UUID, order_id: UUID
    ) -> PaperOrderRow:
        row = await conn.fetchrow(
            "SELECT * FROM paper_sim_orders WHERE order_id = $1 AND account_id = $2",
            order_id,
            account_id,
        )
        if row is None:
            raise PaperOrderNotFoundError(f"order_id={order_id} account_id={account_id}")
        return _row_to_order(row)

    async def find_by_client_id(
        self, conn: asyncpg.Connection, account_id: UUID, client_order_id: str
    ) -> PaperOrderRow | None:
        row = await conn.fetchrow(
            "SELECT * FROM paper_sim_orders WHERE account_id = $1 AND client_order_id = $2",
            account_id,
            client_order_id,
        )
        return None if row is None else _row_to_order(row)

    async def list_orders(
        self,
        conn: asyncpg.Connection,
        account_id: UUID,
        symbol: str | None = None,
        *,
        open_only: bool = True,
    ) -> list[PaperOrderRow]:
        """`open_only=False`면 상태 무관 전체(체결 이력 조회용, `get_fills`가 쓴다)."""
        rows = await conn.fetch(
            "SELECT * FROM paper_sim_orders WHERE account_id = $1 AND ($2::text IS NULL OR "
            "symbol = $2) AND (NOT $3 OR status IN ('ACKNOWLEDGED','PARTIALLY_FILLED')) "
            "ORDER BY created_at",
            account_id,
            symbol,
            open_only,
        )
        return [_row_to_order(row) for row in rows]

    async def apply_fill(
        self,
        conn: asyncpg.Connection,
        *,
        account_id: UUID,
        order_id: UUID,
        expected_version: int,
        fill_price: Decimal,
        fill_quantity: Decimal,
        liquidity: str,
        fee_amount: Decimal,
        fee_currency: str,
        venue_ts: datetime,
    ) -> PaperOrderRow:
        """체결 1건 반영 — 평균단가 재계산, `fills` JSONB append, `version`
        CAS. 잔고 이동은 호출부가 이 결과를 보고 `deposit`/`debit`로 한다."""
        current = await conn.fetchrow(
            "SELECT * FROM paper_sim_orders WHERE order_id = $1 AND account_id = $2 FOR UPDATE",
            order_id,
            account_id,
        )
        if current is None:
            raise PaperOrderNotFoundError(f"order_id={order_id} account_id={account_id}")
        if current["version"] != expected_version:
            raise ConcurrencyConflictError(
                f"paper_sim_orders.order_id={order_id}: "
                f"version {current['version']} != 기대 {expected_version}"
            )

        old_filled = current["filled_quantity"]
        new_filled = old_filled + fill_quantity
        if new_filled > current["quantity"]:
            qty = current["quantity"]
            raise ValueError(f"apply_fill: 누적체결({new_filled}) > 주문수량({qty})")
        old_avg = current["average_fill_price"]
        new_avg = (
            fill_price
            if old_filled == 0 or old_avg is None
            else (old_avg * old_filled + fill_price * fill_quantity) / new_filled
        )
        new_status = "FILLED" if new_filled == current["quantity"] else "PARTIALLY_FILLED"
        raw_fills = current["fills"]
        fills_before = json.loads(raw_fills) if isinstance(raw_fills, str) else list(raw_fills)
        entry = {
            "fill_id": str(uuid4()),
            "price": str(fill_price),
            "quantity": str(fill_quantity),
            "fee": str(fee_amount),
            "fee_currency": fee_currency,
            "liquidity": liquidity,
            "venue_ts": venue_ts.isoformat(),
        }
        new_fills = json.dumps([*fills_before, entry])

        row = await conn.fetchrow(
            "UPDATE paper_sim_orders SET filled_quantity = $3, average_fill_price = $4, "
            "status = $5, fee_total = fee_total + $6, fee_currency = $7, fills = $8::jsonb, "
            "version = version + 1, updated_at = now() "
            "WHERE order_id = $1 AND account_id = $2 AND version = $9 RETURNING *",
            order_id,
            account_id,
            new_filled,
            new_avg,
            new_status,
            fee_amount,
            fee_currency,
            new_fills,
            expected_version,
        )
        if row is None:
            raise ConcurrencyConflictError(f"paper_sim_orders.order_id={order_id}: 동시 갱신 충돌.")
        return _row_to_order(row)

    async def cancel(self, conn: asyncpg.Connection, account_id: UUID, order_id: UUID) -> bool:
        """`cancel_order`가 아닌 `cancel` — fund-moving 게이트는
        `PaperSimulatorAdapter.cancel_order`에 있고, 이 메서드는 그 속성이
        없는 순수 DB CRUD라 AST 스캐너(`test_live_guard_coverage.py`) 이름
        패턴을 의도적으로 피한다."""
        row = await conn.fetchrow(
            "UPDATE paper_sim_orders SET status = 'CANCELLED', version = version + 1, "
            "updated_at = now() WHERE order_id = $1 AND account_id = $2 "
            "AND status IN ('ACKNOWLEDGED','PARTIALLY_FILLED') RETURNING order_id",
            order_id,
            account_id,
        )
        return row is not None
