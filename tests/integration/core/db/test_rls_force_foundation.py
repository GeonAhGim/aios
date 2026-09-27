"""PLT-30 foundation RLS: enabled/FORCE flags, denied app-role DDL, and recovery.

Catalog checks use the migrator connection; authorization checks explicitly switch
into the non-superuser, NOBYPASSRLS aios_app role. Fault injection is transactional
and always rolled back. These tests do not claim cross-tenant row filtering proof.
"""

from __future__ import annotations

import time

import asyncpg
import pytest

from tests.integration.core.db.conftest import AppRoleTx

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


async def _assert_foundation_security(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace "
        "AND relkind = 'r' AND relname = ANY($1::text[])",
        list(_FOUNDATION_TABLES),
    )
    flags = {
        row["relname"]: (row["relrowsecurity"], row["relforcerowsecurity"])
        for row in rows
    }
    assert set(flags) == set(_FOUNDATION_TABLES), flags
    for table, (enabled, forced) in flags.items():
        assert enabled and forced, f"{table}: enabled={enabled}, forced={forced}"


async def test_foundation_tables_have_force_row_level_security(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
@pytest.mark.parametrize(
    "operation",
    [
        "NO FORCE ROW LEVEL SECURITY",
        "DISABLE ROW LEVEL SECURITY",
        "OWNER TO aios_app",
    ],
    ids=["negative-unforce", "negative-disable", "negative-take-ownership"],
)
async def test_negative_app_cannot_weaken_foundation_security(pool, table, operation):
    """I-10: exercise real PostgreSQL authorization for every protected table."""
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        async with AppRoleTx(conn):
            role = await conn.fetchrow(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
            )
            assert role is not None
            assert not role["rolsuper"] and not role["rolbypassrls"]
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                # Both identifiers and operations come exclusively from fixed test tuples.
                await conn.execute(f'ALTER TABLE public."{table}" {operation}')
        await _assert_foundation_security(conn)


@pytest.mark.parametrize("table", _FOUNDATION_TABLES)
async def test_failure_injection_unforce_trips_red_gate_and_rolls_back(pool, table):
    """Inject actual catalog regression; the normal gate must fail, then recover."""
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f'ALTER TABLE public."{table}" NO FORCE ROW LEVEL SECURITY')
            with pytest.raises(AssertionError, match=table):
                await _assert_foundation_security(conn)
        finally:
            await tx.rollback()
        await _assert_foundation_security(conn)


async def test_catalog_security_check_p95_under_borrowed_ack_budget(pool):
    """Borrow ADR-2026-09-09-C's paper ACK p95 50ms budget for catalog reads."""
    durations = []
    async with pool.acquire() as conn:
        await _assert_foundation_security(conn)
        for _ in range(30):
            start = time.perf_counter()
            await _assert_foundation_security(conn)
            durations.append((time.perf_counter() - start) * 1000)
    assert sorted(durations)[28] < 50.0, durations
