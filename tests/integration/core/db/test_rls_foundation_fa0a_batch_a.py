"""FA-0a batch A — tenant_id FK를 users에서 tenant로 바꾼 뒤에도 RLS가
여전히 살아있는지 실DB로 단언한다.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md §9 FA-0a
task-1814 DoD(c). FK 대상만 바꾸고 RLS 정책(tenant_id 값 자체를 비교하는
`current_setting('app.tenant_id')` 술어, b3c7f19ad2e6)까지 깨지 않았음을
증명해야 한다 — FK를 DROP했다가 다시 ADD하는 과정에서 정책이나
FORCE ROW LEVEL SECURITY가 조용히 풀리는 회귀를 잡기 위함이다.
`test_rls_foundation.py`가 이미 `consent_record`/`foundation_audit_event`를
대표로 검증하므로, 이 파일은 그 두 테이블과 겹치지 않는 배치 A의 나머지
RLS 테이블 중 `account_connection`/`risk_evaluation` 두 개를 추가로 검증한다.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg

from tests.integration.conftest import create_test_tenant
from tests.integration.core.db.conftest import AppRoleTx


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
