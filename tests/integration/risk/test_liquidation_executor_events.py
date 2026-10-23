"""task-18977 -- liquidation_executor orders carry order_events (no eventless residue).

`_submit_order` used to INSERT `orders` with its final status and no history,
so `replay_verify` folded those rows to CREATED. Each case below drives the real
worker and proves the fold matches `orders.status` (L4-06 I6, INVARIANTS I-07)."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from scripts.replay_verify import _cutover_at, _order_pair
from src.data.models.trading import Order, OrderStatus
from src.services.safety.liquidation_executor import run_liquidation_worker_once
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.integration.risk.test_liquidation_worker import (
    _clear_stale_requests as _clear_stale_requests,
)
from tests.integration.risk.test_liquidation_worker import _setup
from tests.support.order_cleanup import cleanup_liquidation_request


@pytest.fixture
async def pool():
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def ctx():
    return {"actors": [], "requests": []}


@pytest.fixture(autouse=True)
async def _cleanup(pool, ctx):
    yield
    async with pool.acquire() as conn:
        for request_id in ctx["requests"]:
            await cleanup_liquidation_request(conn, request_id)
        for actor in ctx["actors"]:
            await conn.execute("DELETE FROM positions WHERE user_id = $1", actor)
        await conn.execute(
            "UPDATE safety_control SET state='INACTIVE', deactivated_at=now() WHERE state='ACTIVE'"
        )


async def _orders_and_replay(pool, request_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT order_id, status FROM orders WHERE liquidation_request_id = $1", request_id
        )
        cutover = await _cutover_at(conn)
        out = []
        for r in rows:
            n = await conn.fetchval(
                "SELECT COUNT(*) FROM order_events WHERE order_id = $1", r["order_id"]
            )
            pair = await _order_pair(conn, r["order_id"], cutover_at=cutover)
            assert pair is not None
            out.append((r["status"], n, pair[0]["status"]))
        return out


async def _send_one_slice(pool, ctx, adapter):
    request_id = await _setup(pool, ctx)
    t0 = datetime.now(timezone.utc)
    await run_liquidation_worker_once(pool, {"bitget": adapter}, now=t0)
    await run_liquidation_worker_once(pool, {"bitget": adapter},
                                      now=t0 + timedelta(seconds=60))
    return request_id


async def test_sent_order_has_event_and_replay_matches(pool, ctx):
    request_id = await _send_one_slice(pool, ctx, FakeExchangeAdapter())
    rows = await _orders_and_replay(pool, request_id)
    assert rows
    for status, n_events, replayed in rows:
        assert status == "SUBMITTED"
        assert n_events >= 1
        assert replayed == status


async def test_failed_order_has_event_and_replay_matches(pool, ctx):
    adapter = FakeExchangeAdapter(place_order_result_status=OrderStatus.FAILED)
    request_id = await _send_one_slice(pool, ctx, adapter)
    rows = await _orders_and_replay(pool, request_id)
    assert rows
    for status, n_events, replayed in rows:
        assert status == "FAILED"
        assert n_events >= 1
        assert replayed == status


async def test_adapter_exception_records_failed_event(pool, ctx):
    async def boom(order: Order) -> Order:
        raise RuntimeError("venue down")

    request_id = await _send_one_slice(pool, ctx, FakeExchangeAdapter(on_place_order=boom))
    rows = await _orders_and_replay(pool, request_id)
    assert rows and all(s == "FAILED" and n >= 1 and r == "FAILED" for s, n, r in rows)


async def test_event_write_failure_rolls_back_order_insert(pool, ctx, monkeypatch):
    """Failure injection: if the event append fails the orders INSERT rolls back too."""
    import src.services.safety.liquidation_executor as mod

    async def bad(*_a, **_k):
        raise RuntimeError("event store down")

    monkeypatch.setattr(mod, "update_from_exchange_with_fill_event", bad)
    request_id = await _setup(pool, ctx)
    t0 = datetime.now(timezone.utc)
    await run_liquidation_worker_once(pool, {"bitget": FakeExchangeAdapter()}, now=t0)
    with pytest.raises(RuntimeError):
        await run_liquidation_worker_once(
            pool, {"bitget": FakeExchangeAdapter()}, now=t0 + timedelta(seconds=60)
        )
    async with pool.acquire() as conn:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM orders WHERE liquidation_request_id = $1", request_id
        )
    assert n == 0


@pytest.mark.perf
async def test_send_path_latency_budget(pool, ctx):
    started = time.perf_counter()
    await _send_one_slice(pool, ctx, FakeExchangeAdapter())
    assert time.perf_counter() - started < 5.0
