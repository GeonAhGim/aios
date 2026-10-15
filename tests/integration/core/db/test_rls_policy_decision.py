"""F2(task-12103, tier M) -- `policy_decision` RLS policy + FORCE
(`d3f8a6c91e52`) catalog checks and a non-destructive upgrade/downgrade round
trip.

Spec: docs/audits/AUDIT_2026-10-15_compliance_approval.md F2.
`b3c7f19ad2e6_rls_policies_foundation.py`'s `_FOUNDATION_TABLES` never
included `policy_decision` -- unlike the 8 tables covered there (and unlike
the GUC-binding gap F1/task-9454 fixed on top of an existing policy),
`policy_decision` had no RLS policy at all despite owning a `tenant_id`
column directly. This migration gives it the same policy+ENABLE+FORCE shape
as the foundation 8.

The round trip runs against a disposable DB clone
(`tests/support/db.ensure_worker_database`), never the shared session DB --
see `test_rls_legacy_not_enabled.py`'s module docstring (task-5823) for why a
direct `alembic downgrade`/`upgrade head` against the shared
`TEST_DATABASE_URL` is unsafe for other concurrent readers.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import asyncpg
import pytest

from tests.integration.core.db.conftest import AppRoleTx
from tests.support.db import ensure_worker_database, template_database_url

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_TABLE = "policy_decision"
# d3f8a6c91e52's down_revision -- pinned by id, not "-1", for the same
# reason test_rls_legacy_not_enabled.py pins _PRE_RLS_REVISION (later
# migrations stacking on top of head must not silently retarget this).
_PRE_MIGRATION_REVISION = "fa25b1c9d340"


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


async def test_policy_decision_has_tenant_isolation_policy_enabled_and_forced(pool) -> None:
    assert await _policy_exists(pool, _TABLE)
    enabled, forced = await _relrowsecurity(pool, _TABLE)
    assert enabled and forced, f"{_TABLE}: enabled={enabled}, forced={forced}"


async def test_aios_app_cannot_unforce_policy_decision(pool) -> None:
    """negative: `FORCE`/`ENABLE` toggling is owner-only DDL -- the
    non-superuser, NOBYPASSRLS `aios_app` role must not be able to weaken
    this table's protection (same shape as
    test_rls_force_foundation.py's negative-unforce case)."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"ALTER TABLE {_TABLE} NO FORCE ROW LEVEL SECURITY")


async def test_aios_app_cannot_drop_tenant_isolation_policy_on_policy_decision(pool) -> None:
    """negative: dropping the policy is also owner-only DDL."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(f"DROP POLICY tenant_isolation ON {_TABLE}")


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
    DB. Downgrading one step removes the policy and both flags; upgrading
    back to head restores all three without error."""
    migration_db_url = await ensure_worker_database(template_database_url(), "rlspolicydecision")
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
        finally:
            _run_alembic("upgrade", "head", database_url=migration_db_url)

        assert await _policy_exists(migration_pool, _TABLE)
        enabled, forced = await _relrowsecurity(migration_pool, _TABLE)
        assert enabled and forced
    finally:
        await migration_pool.close()
