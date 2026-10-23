"""R-52 planning tests -- liquidation_worker slice planning only.

Extracted from test_liquidation_worker.py to keep the parent file under the
500-line ratchet threshold (task-18763).  Shared imports/fixtures/helpers are
read-only: _asyncpg_dsn, _cleanup, _clear_stale_requests, _create_request,
_insert_open_position, _request_row, _setup, _slice_states, ctx, pool.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#R-52 (task-2358), §4
`liquidation_request` state table rows 426-435, §9 R-52 DoD."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pytest

from src.services.safety.liquidation_executor import run_liquidation_worker_once
from tests.integration.conftest import create_test_user
from tests.integration.fake_exchange_adapter import FakeExchangeAdapter
from tests.support.order_cleanup import cleanup_liquidation_request


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
            await cleanup_liquidation_request(conn, request_id)
        for actor in ctx["actors"]:
            await conn.execute("DELETE FROM positions WHERE user_id = $1", actor)
        await conn.execute(
            "UPDATE safety_control SET state='INACTIVE', deactivated_at=now() WHERE state='ACTIVE'"
        )


async def test_plans_open_position_into_slices(pool, ctx):
    """§4 PLANNED 전이 -- 요청이 PLANED가 되고 seed_ref가 채워지며
    최소 3개 PENDING 슬라이스가 생성된다."""
    request_id = await _setup(pool, ctx)
    adapters = {"bitget": FakeExchangeAdapter()}

    await run_liquidation_worker_once(pool, adapters, now=datetime.now(timezone.utc))

    row = await _request_row(pool, request_id)
    assert row["state"] == "PLANNED"
    assert row["seed_ref"] is not None
    states = await _slice_states(pool, request_id)
    assert len(states) >= 3
    assert all(s == "PENDING" for s in states)


async def _setup(pool, ctx, *, with_position: bool = True):
    """Helper: create a test user + position + liquidation_request."""
    from uuid import uuid4

    actor = await create_test_user(pool)
    ctx["actors"].append(actor)
    if with_position:
        symbol = f"TL{uuid4().hex[:10]}/USDT"
        await _insert_open_position(pool, actor, symbol)
    return await _create_request(pool, actor, ctx["requests"])


async def _insert_open_position(pool, user_id, symbol):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions (user_id, symbol, exchange, strategy_id, quantity, "
            "average_entry_price, entry_time) "
            "VALUES ($1, $2, 'bitget', 'test-strategy', 1, 50000, now())",
            user_id,
            symbol,
        )


async def _create_request(pool, actor, requests):

    from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
    from src.foundation.risk_gate.application.activate_safety_control import activate_safety_control
    from src.foundation.risk_gate.domain.models import SafetyScope

    repo = PostgresRiskGateRepository(pool)
    view = await activate_safety_control(
        repo,
        tenant_id=actor,
        actor_subject_id=actor,
        actor_is_admin=True,
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(actor),
        reason="test",
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO liquidation_request "
            "(safety_control_id, scope, scope_ref, state, requested_by, fence_token) "
            "VALUES ($1, 'ACCOUNT', $2, 'REQUESTED', 'test', $3) RETURNING id",
            view.id,
            str(actor),
            view.fence_token,
        )
    requests.append(row["id"])
    return row["id"]
