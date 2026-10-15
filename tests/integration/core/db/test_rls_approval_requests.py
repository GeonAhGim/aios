"""F2(task-12104, tier M) -- `approval_requests` tenant_id column + backfill +
RLS policy + FORCE (`a4f27d91c6e8`) catalog checks, cross-tenant negative
tests, and a non-destructive upgrade/downgrade round trip.

Spec: docs/audits/AUDIT_2026-10-15_compliance_approval.md F2(M).
`189bdd25a017`'s `approval_requests` owned only `user_id` (NULL for
scope='PLATFORM') and sat in neither `b3c7f19ad2e6`'s foundation-8 nor
legacy-3 RLS lists -- zero RLS protection since creation. This migration
gives it a real `tenant_id` column (backfilled from the actual `tenant`
table, never assumed equal to `user_id`) plus the same policy+ENABLE+FORCE
shape as the foundation 8, with a NULL-tenant_id system-role exception for
scope='PLATFORM' rows (same shape `b3c7f19ad2e6` already uses for
`foundation_audit_event`).

The round trip runs against a disposable DB clone
(`tests/support/db.ensure_worker_database`), never the shared session DB --
see `test_rls_policy_decision.py`'s module docstring for why a direct
`alembic downgrade`/`upgrade head` against the shared `TEST_DATABASE_URL` is
unsafe for other concurrent readers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.core.db.conftest import AppRoleTx
from tests.support.db import ensure_worker_database, template_database_url

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_TABLE = "approval_requests"
# a4f27d91c6e8's down_revision -- pinned by id (not "-1"), so later
# migrations stacking on top of head cannot silently retarget this.
_PRE_MIGRATION_REVISION = "d3f8a6c91e52"


async def _relrowsecurity(pool: asyncpg.Pool, table: str) -> tuple[bool, bool]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = $1::regclass",
            table,
        )
    assert row is not None
    return bool(row["relrowsecurity"]), bool(row["relforcerowsecurity"])


async def _policy_exists(pool: asyncpg.Pool, table: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT count(*) FROM pg_policies WHERE tablename = $1 AND policyname = "
            "'tenant_isolation'",
            table,
        )
    return bool(value)


async def _insert_request(pool: asyncpg.Pool, tenant_id, *, scope: str = "USER") -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO approval_requests
                (scope, tenant_id, user_id, trigger_source, context, requested_action,
                 approval_mode, mandatory_wait_seconds, expires_at)
            VALUES ($1, $2, $2, 'test', '{}'::jsonb, 'TEST_ACTION', 'SOLO', 60,
                    now() + interval '1 hour')
            RETURNING id
            """,
            scope,
            tenant_id,
        )
    assert row is not None
    return int(row["id"])


async def test_approval_requests_has_tenant_isolation_policy_enabled_and_forced(pool) -> None:
    assert await _policy_exists(pool, _TABLE)
    enabled, forced = await _relrowsecurity(pool, _TABLE)
    assert enabled and forced, f"{_TABLE}: enabled={enabled}, forced={forced}"


async def test_backfill_leaves_zero_null_tenant_id_for_user_owned_rows(pool) -> None:
    """DoD(a): every row with a non-null `user_id` must have a matching
    `tenant_id` after the backfill (PERSONAL tenant's id==user_id
    invariant) -- a NULL here for a user-owned row would mean the backfill
    silently dropped a user's isolation."""
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM approval_requests WHERE tenant_id IS NULL AND user_id IS NOT NULL"
        )
    assert count == 0


async def test_aios_app_cannot_unforce_approval_requests(pool) -> None:
    """negative: `FORCE`/`ENABLE` toggling is owner-only DDL -- the
    non-superuser, NOBYPASSRLS `aios_app` role must not be able to weaken
    this table's protection."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")


async def test_aios_app_cannot_drop_tenant_isolation_policy_on_approval_requests(pool) -> None:
    """negative: dropping the policy is also owner-only DDL."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"DROP POLICY tenant_isolation ON {_TABLE}")


async def test_cross_tenant_select_returns_only_bound_tenant(pool) -> None:
    """DoD(b): a session bound to tenant A must not see tenant B's rows."""
    tenant_a = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    tenant_b = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _insert_request(pool, tenant_a)
    await _insert_request(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch(
            "SELECT tenant_id FROM approval_requests WHERE tenant_id IS NOT NULL"
        )

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_cross_tenant_insert_for_other_tenant_is_rejected(pool) -> None:
    """DoD(b): a session bound to tenant A cannot insert a row claiming
    tenant B via WITH CHECK."""
    tenant_a = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    tenant_b = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO approval_requests
                    (scope, tenant_id, user_id, trigger_source, context, requested_action,
                     approval_mode, mandatory_wait_seconds, expires_at)
                VALUES ('USER', $1, $1, 'test', '{}'::jsonb, 'TEST_ACTION', 'SOLO', 60,
                        now() + interval '1 hour')
                """,
                tenant_b,
            )


async def test_unbound_transaction_returns_nothing(pool) -> None:
    """negative: no `app.tenant_id`/`app.role` bound at all -- fail-closed,
    not fail-open -- must see zero rows even though data exists."""
    tenant_a = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    await _insert_request(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch("SELECT 1 FROM approval_requests WHERE tenant_id IS NOT NULL")

    assert rows == []


async def test_system_role_sees_platform_scope_null_tenant_rows(pool) -> None:
    """scope='PLATFORM' rows are never user-owned and keep `tenant_id IS
    NULL` by design -- only the `system` role carve-out may read them."""
    request_id = await _insert_request(pool, None, scope="PLATFORM")

    async with pool.acquire() as conn, AppRoleTx(conn, system=True):
        row = await conn.fetchrow("SELECT id FROM approval_requests WHERE id = $1", request_id)
    assert row is not None

    async with pool.acquire() as conn, AppRoleTx(conn):
        row = await conn.fetchrow("SELECT id FROM approval_requests WHERE id = $1", request_id)
    assert row is None


async def test_failure_injection_unforce_trips_red_gate_and_rolls_back(pool) -> None:
    """Gate-red reproduction: inject a real catalog regression (owner
    connection, no DDL permission barrier) and confirm the normal assertion
    catches it, then roll back so the injected state never persists."""
    enabled, forced = await _relrowsecurity(pool, _TABLE)
    assert enabled and forced
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")
            row = await conn.fetchrow(
                "SELECT relforcerowsecurity FROM pg_class WHERE oid = $1::regclass", _TABLE
            )
            assert row is not None
            with pytest.raises(AssertionError):
                assert bool(row["relforcerowsecurity"])
        finally:
            await tx.rollback()
    enabled_after, forced_after = await _relrowsecurity(pool, _TABLE)
    assert enabled_after and forced_after


def _run_alembic(*args: str, database_url: str | None = None) -> None:
    env = {**os.environ, "DATABASE_URL": database_url} if database_url else None
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


async def test_upgrade_downgrade_round_trip_is_non_destructive() -> None:
    """Disposable DB clone (task-5823 pattern) -- never the shared session
    DB. Downgrading one step removes the policy, both flags, and the
    `tenant_id` column; upgrading back to head restores all of it and
    re-runs the backfill with zero NULL user-owned rows."""
    migration_db_url = await ensure_worker_database(template_database_url(), "rlsapprovalrequests")
    migration_pool = await asyncpg.create_pool(
        migration_db_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1,
        max_size=2,
    )
    try:
        try:
            _run_alembic("downgrade", _PRE_MIGRATION_REVISION, database_url=migration_db_url)
            assert not await _policy_exists(migration_pool, _TABLE)
            enabled, forced = await _relrowsecurity(migration_pool, _TABLE)
            assert not enabled and not forced
            async with migration_pool.acquire() as conn:
                has_column = await conn.fetchval(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name = $1 AND column_name = 'tenant_id'",
                    _TABLE,
                )
            assert has_column == 0
        finally:
            _run_alembic("upgrade", "head", database_url=migration_db_url)

        assert await _policy_exists(migration_pool, _TABLE)
        enabled, forced = await _relrowsecurity(migration_pool, _TABLE)
        assert enabled and forced
        async with migration_pool.acquire() as conn:
            null_count = await conn.fetchval(
                "SELECT count(*) FROM approval_requests "
                "WHERE tenant_id IS NULL AND user_id IS NOT NULL"
            )
        assert null_count == 0
    finally:
        await migration_pool.close()
