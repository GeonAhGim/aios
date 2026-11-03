"""task-21731 — deterministic repro for the flaky wallet/ledger drift.

`bridge_credit`'s reconcile step used to read `user_wallets` and the ledger
AVAILABLE balance with two unlocked SELECTs. A concurrent `place_hold` commit
landing between them looked like drift, so the reconcile posted a bogus
MANUAL_ADJUSTMENT and left ledger and projection one unit apart
(`user_wallets=1000 != ledger=1001`). The fix locks the ledger row first; this
test pins that ordering without relying on timing luck.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.ledger.adapters.legacy_wallet_bridge import bridge_credit
from src.foundation.ledger.application.purchase_flow import place_hold
from src.foundation.ledger.application.queries import WalletLedgerDriftError, get_balance
from tests.integration.conftest import create_test_user
from tests.integration.foundation.ledger.test_queries import (  # noqa: F401
    _TEST_PURPOSE,
    _clock,
    ports,
)


class _CommitBetweenReadsConn:
    """Proxy that, right after the reconcile's first SELECT, lets a competing
    hold run (and commit if it is not blocked by the reconcile's lock)."""

    def __init__(self, conn, on_first_read) -> None:
        self._conn = conn
        self._on_first_read = on_first_read

    def __getattr__(self, name):
        return getattr(self._conn, name)

    async def fetchval(self, query, *args, **kwargs):
        result = await self._conn.fetchval(query, *args, **kwargs)
        hook, self._on_first_read = self._on_first_read, None
        if hook is not None:
            await hook()
        return result


async def test_hold_committing_between_reconcile_reads_causes_no_drift(pool, ports):  # noqa: F811
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("1000.00"), "TOPUP")

    async def hold() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await place_hold(
                conn,
                buyer_id=buyer,
                amount=Decimal("1.00"),
                purpose=_TEST_PURPOSE,
                reference=f"test-reconcile:{uuid4()}",
                expires_at=_clock() + timedelta(minutes=15),
                actor_subject_id=buyer,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
                holds=ports.holds,
            )
            await conn.execute(
                "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
                buyer,
                Decimal("1.00"),
            )

    hold_task: list[asyncio.Task[None]] = []

    async def run_hold_between_reads() -> None:
        hold_task.append(asyncio.create_task(hold()))
        # unfixed code lets the hold commit here; fixed code blocks it on the lock
        await asyncio.wait(hold_task, timeout=1.0)

    async with pool.acquire() as raw, raw.transaction():
        await bridge_credit(
            _CommitBetweenReadsConn(raw, run_hold_between_reads), buyer, Decimal("1.00"), "TOPUP"
        )
    await asyncio.wait_for(hold_task[0], timeout=10)

    final = await get_balance(pool, buyer, balances=ports.balances)
    assert final.balance == final.available == Decimal("1000.00")
    assert final.held == Decimal("1.00")


async def test_read_path_still_flags_real_drift(pool, ports):  # noqa: F811
    """The lock must not hide genuine drift from the read path."""
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("10.00"), "TOPUP")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_wallets SET balance = balance + 5 WHERE user_id = $1", buyer
        )

    with pytest.raises(WalletLedgerDriftError):
        await get_balance(pool, buyer, balances=ports.balances)
