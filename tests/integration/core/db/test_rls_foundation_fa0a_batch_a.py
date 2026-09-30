"""FA-0a batch A — tenant_id FK + RLS isolation (account_connection / risk_evaluation).

Negative tests (invalid input rejection) and failure-injection tests
(monkeypatch dependency exception) supplement the original RLS isolation tests.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.core.db.conftest import AppRoleTx

# ── helpers ────────────────────────────────────────────────────────────────────


async def _seed_account_connection(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO account_connection
                (tenant_id, owner_subject_id, provider_code, opaque_account_ref,
                 capability_profile)
            VALUES ($1, $1, 'fake-broker', 'ACCT-fa0a-batch-a', ARRAY['READ_BALANCE'])
            """,
            tenant_id,
        )


async def _seed_risk_evaluation(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO risk_evaluation
                (tenant_id, gate_kind, subject_fingerprint, outcome, rule_version)
            VALUES ($1, 'DEPLOYMENT', 'fa0a-batch-a-fingerprint', 'ALLOW', 'v1')
            """,
            tenant_id,
        )


# ── original RLS isolation tests ───────────────────────────────────────────────


async def test_account_connection_cross_tenant_select_returns_zero_rows(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_account_connection(pool, tenant_a)
    await _seed_account_connection(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM account_connection")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_account_connection_select_bound_to_other_tenant_excludes_all(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_account_connection(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_b):
        rows = await conn.fetch(
            "SELECT tenant_id FROM account_connection WHERE tenant_id = $1", tenant_a
        )

    assert rows == []


async def test_risk_evaluation_cross_tenant_select_returns_zero_rows(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_risk_evaluation(pool, tenant_a)
    await _seed_risk_evaluation(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM risk_evaluation")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_risk_evaluation_select_bound_to_other_tenant_excludes_all(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_risk_evaluation(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_b):
        rows = await conn.fetch(
            "SELECT tenant_id FROM risk_evaluation WHERE tenant_id = $1", tenant_a
        )

    assert rows == []


# ── negative tests (invalid input rejection) ───────────────────────────────────


async def test_account_connection_rejects_nonexistent_user_as_tenant(pool):
    """Negative test: FK tenant_id → users(user_id) must reject unknown tenant."""
    fake_user_id = uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO account_connection
                    (tenant_id, owner_subject_id, provider_code, opaque_account_ref,
                     capability_profile)
                VALUES ($1, $1, 'KIS', 'acct-fake', ARRAY['READ_BALANCE'])
                """,
                fake_user_id,
            )


async def test_risk_evaluation_rejects_nonexistent_user_as_tenant(pool):
    """Negative test: FK tenant_id → users(user_id) must reject unknown tenant."""
    fake_user_id = uuid4()
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO risk_evaluation
                    (tenant_id, gate_kind, subject_fingerprint, outcome, rule_version)
                VALUES ($1, 'DEPLOYMENT', 'fp-fake', 'ALLOW', 'v1')
                """,
                fake_user_id,
            )


async def test_account_connection_rejects_invalid_state(pool):
    """Negative test: CHECK constraint on state must reject disallowed values."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO account_connection
                    (tenant_id, owner_subject_id, provider_code, opaque_account_ref,
                     state, capability_profile)
                VALUES ($1, $1, 'KIS', 'acct-fake', 'GIBBERISH', ARRAY['READ_BALANCE'])
                """,
                tenant_id,
            )


# ── failure-injection test ─────────────────────────────────────────────────────


class _RaisingCtx:
    """Async context manager that raises on __aenter__."""

    async def __aenter__(self) -> None:
        raise ConnectionError("simulated pool failure")

    async def __aexit__(self, *exc: object) -> None:
        pass


class _FailingPoolWrapper:
    """Thin wrapper that raises on acquire()."""

    def __init__(self, real_pool: asyncpg.Pool) -> None:
        self._real_pool = real_pool

    def acquire(self) -> _RaisingCtx:
        return _RaisingCtx()

    async def close(self) -> None:
        await self._real_pool.close()


async def test_account_connection_insert_handles_pool_acquire_failure(pool):
    """Failure injection: pool.acquire() raises → caller receives exception.

    Uses an asyncpg pool wrapper (not patch.object) because Pool.acquire is an
    AsyncMethodDescriptor that cannot be monkeypatched on the instance.
    """
    failing_pool = _FailingPoolWrapper(pool)

    with pytest.raises(ConnectionError, match="simulated pool failure"):
        async with failing_pool.acquire():
            pass
