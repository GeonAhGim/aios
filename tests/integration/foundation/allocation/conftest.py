"""FA-8 `allocate_order_fills` 통합테스트 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로
옮겨 두므로, 여기서는 asyncpg DSN 변환과 주문+체결 시딩 헬퍼만 둔다.
"""
from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture(autouse=True)
async def _ledger_control_clean_slate(pool):
    """LC-10 tamper 테스트가 남길 수 있는 `ledger_control.write_frozen`
    잔류를 매 테스트 전후로 초기화한다(`tests/integration/foundation/ledger
    /conftest.py`와 동일 이유 — 원장 전역 상태 공유 오염 방지)."""

    async def _reset() -> None:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE ledger_control SET write_frozen = FALSE, frozen_reason = NULL, "
                "frozen_at = NULL WHERE id = 1"
            )

    await _reset()
    yield
    await _reset()


async def create_order_with_fills(
    pool: asyncpg.Pool,
    *,
    user_id: UUID,
    fund_id: UUID,
    portfolio_id: UUID,
    side: str = "BUY",
    fills: list[tuple[Decimal, Decimal]],
) -> UUID:
    """`orders` 1행 + `fills` N행을 시딩하고 `order_id`를 반환한다.
    `fills`는 (quantity, price) 튜플 목록이다."""
    order_id = uuid4()
    total_quantity = sum((q for q, _ in fills), Decimal("0"))
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO orders "
            "(order_id, user_id, client_order_id, strategy_id, strategy_version, symbol, "
            " exchange, side, order_type, quantity, status, fund_id, portfolio_id) "
            "VALUES ($1, $2, $3, 'fa8-test', 'v1', 'AAPL', 'NASDAQ', $4, 'MARKET', $5, "
            " 'FILLED', $6, $7)",
            order_id, user_id, f"fa8-test-{order_id}", side, total_quantity,
            fund_id, portfolio_id,
        )
        for i, (quantity, price) in enumerate(fills):
            await conn.execute(
                "INSERT INTO fills "
                "(provider_fill_id, venue, order_id, exchange_order_id, symbol, side, "
                " quantity, price, fee, fee_currency, liquidity, venue_ts) "
                "VALUES ($1, 'NASDAQ', $2, 'ext-order-1', 'AAPL', $3, $4, $5, 0, 'USD', "
                " 'TAKER', now())",
                f"fa8-fill-{order_id}-{i}", order_id, side, quantity, price,
            )
    return order_id


async def wallet_available_balance(pool: asyncpg.Pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id "
            "WHERE la.account_code = $1",
            f"USER:{user_id}:AVAILABLE",
        )
    return value if value is not None else Decimal("0")


__all__ = [
    "create_order_with_fills",
    "create_test_user",
    "wallet_available_balance",
]
