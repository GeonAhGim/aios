"""PLT-30 — foundation 8 테이블 RLS 강제 + `tenant_transaction`/
`system_transaction` GUC 바인딩.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2 M5,
§9 PLT-30 DoD("WHERE 없는 SELECT가 0행, aios_app role로 교차 테넌트 접근
차단"), §8 `test_rls_scoping.py` 예시.

대표로 `consent_record`(일반 8개 중 하나)와 `foundation_audit_event`(NULL
tenant_id 예외를 갖는 유일한 테이블)를 검증한다 — 나머지 6개는 같은 M5
정책 생성 함수로 만들어진 동일한 형태의 정책이라 회귀 위험이 이 두 케이스와
다르지 않다.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.db.tenant_scope import system_transaction, tenant_transaction
from tests.foundation.integration.trust.conftest import create_disclosure, unique_purpose
from tests.integration.conftest import create_test_user
from tests.integration.core.db.conftest import AppRoleTx


async def _seed_consent(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    purpose = unique_purpose()
    disclosure_id = await create_disclosure(pool, purpose=purpose)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO consent_record "
            "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
            "VALUES ($1, $1, $2, $3, 1)",
            tenant_id,
            purpose,
            disclosure_id,
        )


async def _seed_audit_event(
    pool: asyncpg.Pool, tenant_id: UUID | None, sequence_no: int
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO foundation_audit_event "
            "(tenant_id, sequence_no, aggregate_type, aggregate_id, action, outcome, "
            "trace_id, payload_hash, payload, event_hash) "
            "VALUES ($1, $2, 'test.aggregate', gen_random_uuid(), 'test.action', "
            "'SUCCESS', gen_random_uuid(), 'hash', '{}'::jsonb, 'hash')",
            tenant_id,
            sequence_no,
        )


def _sequence_no() -> int:
    return uuid4().int % 1_000_000_000


async def test_tenant_transaction_binds_app_tenant_id_guc(pool):
    tenant_a = await create_test_user(pool)
    async with tenant_transaction(pool, tenant_a) as conn:
        value = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert value == str(tenant_a)


async def test_tenant_transaction_with_none_binds_empty_string(pool):
    async with tenant_transaction(pool, None) as conn:
        value = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert value == ""


async def test_system_transaction_binds_role_system(pool):
    async with system_transaction(pool) as conn:
        role = await conn.fetchval("SELECT current_setting('app.role', true)")
        tenant = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert role == "system"
    assert tenant == ""


async def test_select_without_where_returns_only_bound_tenant(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    await _seed_consent(pool, tenant_a)
    await _seed_consent(pool, tenant_b)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM consent_record")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_unbound_transaction_returns_nothing(pool):
    tenant_a = await create_test_user(pool)
    await _seed_consent(pool, tenant_a)

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch("SELECT 1 FROM consent_record")

    assert rows == []


async def test_insert_for_other_tenant_is_rejected(pool):
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)
    disclosure_id = await create_disclosure(pool, purpose=unique_purpose())

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO consent_record "
                "(tenant_id, subject_id, purpose, disclosure_id, disclosure_revision) "
                "VALUES ($1, $1, 'other-tenant-purpose', $2, 1)",
                tenant_b,
                disclosure_id,
            )


async def test_system_role_reads_null_tenant_audit_event_only(pool):
    tenant_a = await create_test_user(pool)
    await _seed_audit_event(pool, tenant_a, _sequence_no())
    await _seed_audit_event(pool, None, _sequence_no())

    async with pool.acquire() as conn, AppRoleTx(conn, system=True):
        rows = await conn.fetch("SELECT tenant_id FROM foundation_audit_event")

    assert rows
    assert {r["tenant_id"] for r in rows} == {None}


async def test_ordinary_tenant_binding_excludes_null_tenant_audit_event(pool):
    tenant_a = await create_test_user(pool)
    await _seed_audit_event(pool, tenant_a, _sequence_no())
    await _seed_audit_event(pool, None, _sequence_no())

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM foundation_audit_event")

    assert {r["tenant_id"] for r in rows} == {tenant_a}
