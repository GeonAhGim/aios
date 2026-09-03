"""PLT-30 — 레거시 테이블(orders/positions/strategy_executions)은 정책만
갖고 RLS는 비활성 상태로 남는다 + M5 마이그레이션 upgrade/downgrade 왕복.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §10 리스크1
("레거시 테이블은 정책만 만들고 ENABLE하지 않는다 — 기존 pool.acquire()
경로가 0행을 받아 깨지는 것을 막는다"), §9 PLT-30 DoD("upgrade/downgrade
왕복").
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from tests.integration.conftest import create_test_user
from tests.integration.core.db.conftest import AppRoleTx

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_LEGACY_TABLES = ("orders", "positions", "strategy_executions")
_FOUNDATION_TABLES = (
    "consent_record",
    "account_connection",
    "risk_evaluation",
    "paper_deployment",
    "reconciliation_run",
    "valuation_snapshot",
    "portfolio_mandate",
    "foundation_audit_event",
)


async def _insert_position(pool: asyncpg.Pool, user_id: UUID, strategy_id: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO positions "
            "(user_id, symbol, exchange, strategy_id, quantity, average_entry_price, "
            "entry_time) "
            "VALUES ($1, 'BTC/USDT', 'bitget', $2, 1, 100, now())",
            user_id,
            strategy_id,
        )


async def _relrowsecurity(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", table
        )
    return bool(value)


async def _policy_exists(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE tablename = $1 AND policyname = "
            "'tenant_isolation'",
            table,
        )
    return bool(value)


@pytest.mark.parametrize("table", _LEGACY_TABLES)
async def test_legacy_table_has_policy_but_rls_disabled(pool, table):
    assert await _policy_exists(pool, table)
    assert await _relrowsecurity(pool, table) is False


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
async def test_foundation_table_has_policy_and_rls_enabled(pool, table):
    assert await _policy_exists(pool, table)
    assert await _relrowsecurity(pool, table) is True


async def test_legacy_pool_acquire_without_tenant_binding_still_sees_all_rows(pool):
    """RLS가 걸렸다면 GUC 미설정 시 0행이 됐을 것 — 레거시 테이블은 정책이
    있어도 ENABLE하지 않았으므로 기존 서비스 경로(트랜잭션 밖 pool.acquire(),
    app.tenant_id 미설정)가 그대로 동작해야 한다."""
    user_a = await create_test_user(pool)
    user_b = await create_test_user(pool)
    strategy_id = f"rls-legacy-{user_a.hex[:8]}"
    await _insert_position(pool, user_a, strategy_id)
    await _insert_position(pool, user_b, strategy_id)

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch(
            "SELECT user_id FROM positions WHERE strategy_id = $1", strategy_id
        )

    assert {r["user_id"] for r in rows} == {user_a, user_b}


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


async def test_upgrade_downgrade_round_trip(pool):
    try:
        _run_alembic("downgrade", "-1")
        for table in _FOUNDATION_TABLES:
            assert await _relrowsecurity(pool, table) is False
            assert not await _policy_exists(pool, table)
        for table in _LEGACY_TABLES:
            assert not await _policy_exists(pool, table)
    finally:
        _run_alembic("upgrade", "head")

    for table in _FOUNDATION_TABLES:
        assert await _relrowsecurity(pool, table) is True
        assert await _policy_exists(pool, table)
    for table in _LEGACY_TABLES:
        assert await _policy_exists(pool, table)
        assert await _relrowsecurity(pool, table) is False
