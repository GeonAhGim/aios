"""task-21885 — deterministic regression for the `_reconcile_available` advisory lock.

`purchase_flow._reconcile_available` reads `user_wallets.balance` and the ledger
AVAILABLE balance with two SELECTs. Without the advisory lock (task-21869), a
writer committing between the two reads looks like drift and the reconcile
posts a bogus MANUAL_ADJUSTMENT. These tests inject that commit between the
reads from a second connection: removing the lock records a drift row (test
fails with direction/size/writer kind in the message); with the lock the
writer blocks until the reconcile transaction ends and no drift row exists.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.adapters.legacy_wallet_bridge import bridge_credit
from src.foundation.ledger.application.purchase_flow import _reconcile_available, place_hold
from tests.integration.conftest import create_test_user
from tests.integration.foundation.ledger.test_queries import (  # noqa: F401
    _TEST_PURPOSE,
    _clock,
    ports,
)

_START = Decimal("1000.00")
_DELTA = Decimal("50.00")


class _CommitBetweenReadsConn:
    """Proxy that runs a competing writer right after the first `fetchval`
    (the `user_wallets` read), i.e. between the reconcile's two reads."""

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


async def _drift_rows(pool, buyer) -> list[tuple[str, Decimal]]:
    """Lines of the reconcile's own sync entries (`purchase_flow:legacy_sync:`)
    against the buyer's AVAILABLE account."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT pl.side, pl.amount FROM ledger_journal_entry e "
            "JOIN ledger_posting_line pl ON pl.entry_id = e.entry_id "
            "JOIN ledger_account la ON la.account_id = pl.account_id "
            "WHERE e.event_type = 'MANUAL_ADJUSTMENT' "
            "AND e.event_ref LIKE $1 AND la.account_code = $2 ORDER BY e.sequence_no",
            f"purchase_flow:legacy_sync:{buyer}:%",
            f"USER:{buyer}:AVAILABLE",
        )
    return [(r["side"], r["amount"]) for r in rows]


@pytest.mark.parametrize("writer_kind", ["credit", "hold"])
async def test_writer_committing_between_reconcile_reads_records_no_drift(
    pool,
    ports,  # noqa: F811
    writer_kind,
):
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, _START, "TOPUP")

    async def credit() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await bridge_credit(conn, buyer, _DELTA, "TOPUP")

    async def hold() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await place_hold(
                conn,
                buyer_id=buyer,
                amount=_DELTA,
                purpose=_TEST_PURPOSE,
                reference=f"test-reconcile-lock:{uuid4()}",
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
                _DELTA,
            )

    writer = credit if writer_kind == "credit" else hold
    tasks: list[asyncio.Task[None]] = []

    async def run_writer_between_reads() -> None:
        tasks.append(asyncio.create_task(writer()))
        # no lock: the writer commits within this window; lock: it stays blocked
        await asyncio.wait(tasks, timeout=1.0)

    async with pool.acquire() as raw, raw.transaction():
        await _reconcile_available(
            _CommitBetweenReadsConn(raw, run_writer_between_reads),
            buyer,
            Currency.KRW,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )
    await asyncio.wait_for(tasks[0], timeout=10)

    drift = await _drift_rows(pool, buyer)
    assert not drift, (
        f"false drift recorded by reconcile (writer={writer_kind}): "
        f"wallet read before writer, ledger read after writer commit of "
        f"{'+' if writer_kind == 'credit' else '-'}{_DELTA} -> "
        f"drift rows (side, amount)={drift}; expected 0 rows"
    )
