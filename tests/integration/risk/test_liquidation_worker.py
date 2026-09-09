"""R-52 통합테스트 -- liquidation_executor.run_liquidation_worker_once().

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-52 (task-2358), §4
`liquidation_request` state table rows 426-435, §9 R-52 DoD (fence 변경
ABORTED, deadline 시장가 폴백 1회, `is_liquidation` 주문은 request 참조)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.liquidation_executor import run_liquidation_worker_once
from src.services.safety.liquidation_planning import LiquidationSeedKeyMissingError
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


async def _create_request(pool: asyncpg.Pool, actor: UUID, requests: list[UUID]) -> UUID:
    """§4 row 430 -- what watchdog_process._apply_decision does for LIQUIDATE."""
    repo = PostgresRiskGateRepository(pool)
    view = await activate_safety_control(
        repo, tenant_id=actor, actor_subject_id=actor, actor_is_admin=True,
        scope=SafetyScope.GLOBAL, scope_ref=None, reason="test",
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO liquidation_request "
            "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
            "VALUES ($1, 'GLOBAL', '', 'REQUESTED', 'test', $2) RETURNING id",
            view.id, view.fence_token,
        )
    requests.append(row["id"])
    return row["id"]


async def _insert_open_position(
    pool: asyncpg.Pool, user_id: UUID, *, symbol: str = "BTC/USDT"
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', 1, 50000, now())",
            user_id, symbol,
        )


async def _setup(pool: asyncpg.Pool, ctx: dict, *, with_position: bool = True) -> UUID:
    """§4 GLOBAL scope has no scope_ref filter -- `_load_positions` sees
    every account's open positions, so each test needs its own isolated
    `positions`/`liquidation_request` rows cleaned up afterward (`ctx`,
    consumed by the `_cleanup` fixture) or tests contaminate each other."""
    actor = await create_test_user(pool)
    ctx["actors"].append(actor)
    if with_position:
        await _insert_open_position(pool, actor)
    return await _create_request(pool, actor, ctx["requests"])


async def _request_row(pool: asyncpg.Pool, request_id: UUID) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM liquidation_request WHERE id = $1", request_id)


async def _slice_states(pool: asyncpg.Pool, request_id: UUID) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT state FROM liquidation_slice WHERE request_id = $1 ORDER BY seq", request_id
        )
    return [r["state"] for r in rows]


@pytest.fixture
def ctx():
    return {"actors": [], "requests": []}


@pytest.fixture(autouse=True)
async def _cleanup(pool, ctx):
    yield
    async with pool.acquire() as conn:
        for request_id in ctx["requests"]:
            await conn.execute(
                "UPDATE liquidation_slice SET order_id = NULL WHERE request_id = $1", request_id
            )
            await conn.execute("DELETE FROM orders WHERE liquidation_request_id = $1", request_id)
            await conn.execute("DELETE FROM liquidation_slice WHERE request_id = $1", request_id)
            await conn.execute("DELETE FROM liquidation_request WHERE id = $1", request_id)
        for actor in ctx["actors"]:
            await conn.execute("DELETE FROM positions WHERE user_id = $1", actor)
        await conn.execute(
            "UPDATE safety_control SET state='INACTIVE', deactivated_at=now() WHERE state='ACTIVE'"
        )


async def test_seed_key_missing_fails_closed(pool, monkeypatch):
    monkeypatch.delenv("AIOS_LIQUIDATION_SEED_KEY", raising=False)
    with pytest.raises(LiquidationSeedKeyMissingError):
        await run_liquidation_worker_once(pool, {}, now=datetime.now(timezone.utc))


async def test_no_open_positions_marks_request_done(pool, ctx):
    request_id = await _setup(pool, ctx, with_position=False)
    adapters = {"bitget": FakeExchangeAdapter()}

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "DONE"


async def test_plans_open_position_into_slices(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "PLANNED"
    assert row["seed_ref"] is not None
    states = await _slice_states(pool, request_id)
    assert len(states) >= 3
    assert all(s == "PENDING" for s in states)


async def test_fence_change_aborts_request_and_skips_slices(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    actor = ctx["actors"][0]

    # Plan it first so there are PENDING slices to abort.
    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))
    assert (await _request_row(pool, request_id))["state"] == "PLANNED"

    # A brand-new control bumps the GLOBAL fence past this request's snapshot.
    repo = PostgresRiskGateRepository(pool)
    await activate_safety_control(
        repo, tenant_id=actor, actor_subject_id=actor, actor_is_admin=True,
        scope=SafetyScope.GLOBAL, scope_ref=None, reason="supersede",
    )

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "ABORTED"
    assert row["completed_at"] is not None
    assert all(s == "SKIPPED" for s in await _slice_states(pool, request_id))


async def test_due_slice_sends_liquidation_order_referencing_request(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    t0 = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED
    await run_liquidation_worker_once(pool, adapters, now=t0 + timedelta(seconds=60))  # send seq 0

    row = await _request_row(pool, request_id)
    assert row["state"] == "EXECUTING"
    states = await _slice_states(pool, request_id)
    assert states[0] == "SENT"

    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            "SELECT is_liquidation, liquidation_request_id, status "
            "FROM orders WHERE liquidation_request_id = $1", request_id,
        )
    assert order is not None
    assert order["is_liquidation"] is True
    assert order["liquidation_request_id"] == request_id
    assert order["status"] == "SUBMITTED"


async def test_deadline_sends_single_market_fallback_and_completes(pool, ctx):
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}
    t0 = datetime.now(timezone.utc)

    await run_liquidation_worker_once(pool, adapters, now=t0)  # REQUESTED -> PLANNED
    row = await _request_row(pool, request_id)
    deadline_at = row["requested_at"] + timedelta(seconds=300)

    await run_liquidation_worker_once(pool, adapters, now=deadline_at + timedelta(seconds=1))

    row = await _request_row(pool, request_id)
    assert row["state"] == "DONE"
    assert all(s == "SENT" for s in await _slice_states(pool, request_id))

    async with pool.acquire() as conn:
        orders = await conn.fetch(
            "SELECT order_type, quantity, is_liquidation, liquidation_request_id "
            "FROM orders WHERE liquidation_request_id = $1", request_id,
        )
    assert len(orders) == 1  # single consolidated fallback order, not one per slice
    assert orders[0]["order_type"] == "MARKET"
    assert orders[0]["quantity"] == Decimal("1.0000000000")
    assert orders[0]["is_liquidation"] is True
