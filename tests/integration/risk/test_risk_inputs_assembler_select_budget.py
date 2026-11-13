"""R-31 SELECT-budget negative / gate-red evidence for `assemble_risk_inputs`.

Split from `test_risk_inputs_assembler.py::test_two_select_round_trips`
(file stays under the 500-line threshold). Spec: L4_risk_and_safety §3.5.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import asyncpg
import pytest

from src.data.models.trading import AccountBalance
from src.services.execution_loop.risk_inputs_assembler import assemble_risk_inputs
from tests.integration.conftest import create_test_user
from tests.integration.risk._risk_inputs_assembler_fixtures import (
    NOW,
    POLICY,
    asyncpg_dsn,
    caches,
    candles,
    create_execution,
    intent,
)


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


def _count_selects(monkeypatch) -> list[str]:
    """Record every SELECT/WITH issued through asyncpg fetch/fetchrow."""
    queries: list[str] = []
    original_fetchrow = asyncpg.Connection.fetchrow
    original_fetch = asyncpg.Connection.fetch

    async def counting_fetchrow(self, query, *args, **kwargs):
        queries.append(query)
        return await original_fetchrow(self, query, *args, **kwargs)

    async def counting_fetch(self, query, *args, **kwargs):
        queries.append(query)
        return await original_fetch(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", counting_fetchrow)
    monkeypatch.setattr(asyncpg.Connection, "fetch", counting_fetch)
    return queries


def _selects(queries: list[str]) -> int:
    return sum(1 for q in queries if q.strip().upper().startswith(("SELECT", "WITH")))


async def _seeded_assemble(pool, monkeypatch, strategy_id, *, n_candles=3, calls=1):
    user_id = await create_test_user(pool)
    execution_id = await create_execution(pool, user_id)
    symbol = "BTC/USDT"
    queries = _count_selects(monkeypatch)
    riskcaches = caches()
    riskcaches.equity_tracker.seed(
        execution_id,
        day_start_date=date(2026, 1, 1),
        day_start_equity=Decimal("1000"),
        peak_equity=Decimal("1000"),
    )
    for _ in range(calls):
        await assemble_risk_inputs(
            pool,
            riskcaches,
            execution_id=execution_id,
            user_id=user_id,
            intent=intent(symbol, strategy_id),
            balances=[
                AccountBalance(
                    exchange="bitget", asset="USDT", total=Decimal("1000"), available=Decimal("900")
                )
            ],
            candles=candles(symbol, "bitget", n_candles),
            policy=POLICY,
            now=NOW,
        )
    return queries


async def test_select_budget_is_per_call_not_cumulative(pool, monkeypatch):
    """Negative — two consecutive calls cost 2 SELECTs each (4 total); no call
    may hide queries by riding on a cache warmed by the previous one."""
    queries = await _seeded_assemble(pool, monkeypatch, "strat-count-2x", calls=2)
    assert _selects(queries) == 4, queries


async def test_select_budget_holds_with_minimal_candles(pool, monkeypatch):
    """Negative — a single-candle (degenerate) input must not add round trips."""
    queries = await _seeded_assemble(pool, monkeypatch, "strat-count-1c", n_candles=1)
    assert _selects(queries) == 2, queries


async def test_select_budget_gate_red_when_extra_select_injected(pool, monkeypatch):
    """Gate-red reproduction — one extra real SELECT issued alongside the
    assembly breaks the `== 2` assertion of test_two_select_round_trips."""
    queries = await _seeded_assemble(pool, monkeypatch, "strat-count-red")
    assert _selects(queries) == 2
    async with pool.acquire() as conn:
        await conn.fetchrow("SELECT 1")
    with pytest.raises(AssertionError):
        assert _selects(queries) == 2, queries
