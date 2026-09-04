"""legacy_execution_pauser 통합테스트 — 실제 TEST_DATABASE_URL 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §3.8 (R-38). 5개 SafetyScope
매핑, 행별 조건부 UPDATE, 타 테넌트 미영향, 멱등성을 실제 Postgres 행으로
검증한다 — 조건절이 맞는지는 순수 로직만으로는 증명되지 않는다."""
from __future__ import annotations

import json
import os
import uuid
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.safety.legacy_execution_pauser import (
    MalformedScopeRefError,
    UnmappedSafetyScopeError,
    pause_executions_for_scope,
)
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


async def _seed_execution(
    pool: asyncpg.Pool,
    user_id: UUID,
    *,
    exchange: str = "bitget",
    status: str = "RUNNING",
) -> int:
    strategy_id = f"pauser-test-{uuid.uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent, lifecycle_status)
            VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', $3, $4::jsonb,
                    'test-author', 'APPROVED')
            """,
            strategy_id,
            user_id,
            exchange,
            json.dumps({}),
        )
        row = await conn.fetchrow(
            """
            INSERT INTO strategy_executions
                (strategy_id, strategy_version, user_id, exchange, mode,
                 allocated_capital, currency, status)
            VALUES ($1, '1.0.0', $2, $3, 'PAPER', $4, 'USDT', $5)
            RETURNING id
            """,
            strategy_id,
            user_id,
            exchange,
            Decimal("500"),
            status,
        )
    return row["id"]


async def _status_of(pool: asyncpg.Pool, execution_id: int) -> tuple[str, str | None]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, paused_by FROM strategy_executions WHERE id = $1", execution_id
        )
    assert row is not None
    return row["status"], row["paused_by"]


async def test_global_scope_pauses_every_running_execution_across_tenants(pool):
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, user_a)
    exec_b = await _seed_execution(pool, user_b, exchange="binance")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.GLOBAL, "", control_id=uuid4()
        )

    assert set(paused) >= {exec_a, exec_b}
    assert await _status_of(pool, exec_a) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, exec_b) == ("PAUSED", "SAFETY_LAYER")


async def test_provider_scope_pauses_only_matching_exchange(pool):
    user_id = await create_test_user(pool)
    bitget_exec = await _seed_execution(pool, user_id, exchange="bitget")
    binance_exec = await _seed_execution(pool, user_id, exchange="binance")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.PROVIDER, "bitget", control_id=uuid4()
        )

    assert paused == [bitget_exec]
    assert await _status_of(pool, bitget_exec) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, binance_exec) == ("RUNNING", None)


async def test_tenant_scope_does_not_affect_other_tenants_execution(pool):
    """negative test — 타 테넌트 execution 미영향(DoD 필수 항목)."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, tenant_a)
    exec_b = await _seed_execution(pool, tenant_b)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(tenant_a), control_id=uuid4()
        )

    assert paused == [exec_a]
    assert await _status_of(pool, exec_a) == ("PAUSED", "SAFETY_LAYER")
    assert await _status_of(pool, exec_b) == ("RUNNING", None)


async def test_account_scope_pauses_only_that_accounts_execution(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    exec_a = await _seed_execution(pool, tenant_a)
    exec_b = await _seed_execution(pool, tenant_b)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.ACCOUNT, str(tenant_a), control_id=uuid4()
        )

    assert paused == [exec_a]
    assert await _status_of(pool, exec_b) == ("RUNNING", None)


async def test_strategy_deployment_exec_prefix_pauses_only_that_execution(pool):
    user_id = await create_test_user(pool)
    target = await _seed_execution(pool, user_id)
    other = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.STRATEGY_DEPLOYMENT, f"exec:{target}", control_id=uuid4()
        )

    assert paused == [target]
    assert await _status_of(pool, other) == ("RUNNING", None)


async def test_strategy_deployment_dep_prefix_is_paper_control_target_not_legacy(pool):
    """§3.8 표: STRATEGY_DEPLOYMENT의 dep:<uuid>는 paper_control.deployment_id
    대상이다 — legacy strategy_executions에는 대응 행이 없으므로 0건이
    정답이다(매핑 누락이 아니라 명시적 무관)."""
    user_id = await create_test_user(pool)
    running = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.STRATEGY_DEPLOYMENT, f"dep:{uuid4()}", control_id=uuid4()
        )

    assert paused == []
    assert await _status_of(pool, running) == ("RUNNING", None)


async def test_pausing_an_already_paused_execution_is_idempotent(pool):
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id, status="PAUSED")

    async with pool.acquire() as conn:
        paused = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )

    assert paused == []
    assert await _status_of(pool, exec_id) == ("PAUSED", None)


async def test_repeated_call_after_first_pause_returns_empty_second_time(pool):
    user_id = await create_test_user(pool)
    exec_id = await _seed_execution(pool, user_id)

    async with pool.acquire() as conn:
        first = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )
        second = await pause_executions_for_scope(
            conn, SafetyScope.TENANT, str(user_id), control_id=uuid4()
        )

    assert first == [exec_id]
    assert second == []


async def test_unmapped_scope_raises_instead_of_silently_matching_zero_rows(pool):
    async with pool.acquire() as conn:
        with pytest.raises(UnmappedSafetyScopeError):
            await pause_executions_for_scope(
                conn, "BOGUS_SCOPE", "irrelevant", control_id=uuid4()  # type: ignore[arg-type]
            )


async def test_tenant_scope_rejects_non_uuid_scope_ref(pool):
    async with pool.acquire() as conn:
        with pytest.raises(MalformedScopeRefError):
            await pause_executions_for_scope(
                conn, SafetyScope.TENANT, "not-a-uuid", control_id=uuid4()
            )


async def test_strategy_deployment_rejects_malformed_scope_ref(pool):
    async with pool.acquire() as conn:
        with pytest.raises(MalformedScopeRefError):
            await pause_executions_for_scope(
                conn, SafetyScope.STRATEGY_DEPLOYMENT, "garbage", control_id=uuid4()
            )
