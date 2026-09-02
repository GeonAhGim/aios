"""LC-12(a) `legacy_wallet_bridge` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§5.4(3단계), §9 LC-12.
DoD: "브리지 후 잔액 = ledger_balance 잔액", "wallet_transactions 행 계속
생성", 잔액 부족은 fail-closed(투영 미변경, `BridgeInsufficientBalanceError`).
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import pytest

from src.foundation.ledger.adapters.legacy_wallet_bridge import (
    BridgeInsufficientBalanceError,
    bridge_credit,
    bridge_debit,
)
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from tests.integration.conftest import create_test_user


async def _ledger_balance(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(user_id, UserSub.AVAILABLE),
        )
    return value if value is not None else Decimal("0")


async def _wallet_balance(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        )
    return value if value is not None else Decimal("0")


async def test_bridge_credit_matches_ledger_and_projects_wallet_transactions(pool):
    user = await create_test_user(pool)

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_credit(conn, user, Decimal("300.00"), "TOPUP")

    assert balance_after == Decimal("300.00")
    assert await _wallet_balance(pool, user) == Decimal("300.00")
    assert await _ledger_balance(pool, user) == Decimal("300.00")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT tx_type, amount, balance_after, related_purchase_id "
            "FROM wallet_transactions WHERE user_id = $1",
            user,
        )
    assert row["tx_type"] == "TOPUP"
    assert row["amount"] == Decimal("300.00")
    assert row["balance_after"] == Decimal("300.00")
    assert row["related_purchase_id"] is None


async def test_bridge_debit_matches_ledger_after_credit(pool):
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("500.00"), "TOPUP")

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_debit(conn, user, Decimal("120.00"), "PURCHASE_DEBIT")

    assert balance_after == Decimal("380.00")
    assert await _wallet_balance(pool, user) == Decimal("380.00")
    assert await _ledger_balance(pool, user) == Decimal("380.00")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT amount, related_purchase_id FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert row["amount"] == Decimal("-120.00")
    assert row["related_purchase_id"] is None


async def test_bridge_debit_insufficient_balance_rolls_back_without_projecting(pool):
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("50.00"), "TOPUP")

    with pytest.raises(BridgeInsufficientBalanceError):
        async with pool.acquire() as conn, conn.transaction():
            await bridge_debit(conn, user, Decimal("999.00"), "PURCHASE_DEBIT")

    # 실패한 시도가 잔액·투영을 조금도 건드리지 않았어야 한다(fail-closed).
    assert await _wallet_balance(pool, user) == Decimal("50.00")
    assert await _ledger_balance(pool, user) == Decimal("50.00")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM wallet_transactions "
            "WHERE user_id = $1 AND tx_type = 'PURCHASE_DEBIT'",
            user,
        )
    assert count == 0


async def test_bridge_credit_creates_wallet_row_for_brand_new_user(pool):
    user = await create_test_user(pool)

    async with pool.acquire() as conn, conn.transaction():
        balance_after = await bridge_credit(conn, user, Decimal("10.00"), "SALE_CREDIT")

    assert balance_after == Decimal("10.00")
    assert await _wallet_balance(pool, user) == Decimal("10.00")
    assert await _ledger_balance(pool, user) == Decimal("10.00")
