"""PLT-30 적대적 — `aios_app`의 RLS 우회 시도와 GUC 누수 시도가 모두 막힌다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §8
`test_rls_bypass_attempt.py`("SET LOCAL app.tenant_id를 다른 값으로 두 번
설정해도 트랜잭션 밖으로 누수 없음; RESET 후 0행"), §2 표 448행("I3
tenant-scoped 테이블은 app.tenant_id와 다른 행을 반환하지 않는다 — DB(RLS
policy, aios_app NOBYPASSRLS)").

`ALTER TABLE ... DISABLE/DROP POLICY`는 테이블 소유자 전용 DDL이라
`aios_app`(DML 전용, 소유권 없음)은 WORM 트리거 우회 시도
(tests/adversarial/ledger/test_role_bypass.py)와 동일하게 권한 검사
자체에서 막혀야 한다 — 트리거·정책을 실제로 비활성화하는 시도가 아니라
그 이전 단계에서 거부되는지를 확인한다.
"""

from __future__ import annotations

from uuid import UUID

import asyncpg
import pytest

from src.core.db.tenant_scope import tenant_transaction
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


async def test_aios_app_cannot_disable_rls_on_consent_record(pool):
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("ALTER TABLE consent_record DISABLE ROW LEVEL SECURITY")


async def test_aios_app_cannot_drop_tenant_isolation_policy(pool):
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("DROP POLICY tenant_isolation ON consent_record")


async def test_aios_app_cannot_create_permissive_bypass_policy(pool):
    """`USING (true)`인 추가 정책을 몰래 얹어 사실상 RLS를 무력화하려는
    시도 — `CREATE POLICY`도 DDL이라 소유자 전용이다."""
    async with pool.acquire() as conn, AppRoleTx(conn):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "CREATE POLICY bypass_everything ON consent_record USING (true)"
            )


async def test_tenant_transaction_guc_does_not_leak_to_next_transaction(pool):
    tenant_a = await create_test_user(pool)
    await _seed_consent(pool, tenant_a)

    async with tenant_transaction(pool, tenant_a) as conn:
        bound = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert bound == str(tenant_a)

    async with pool.acquire() as conn:
        leaked = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert leaked in (None, "")

    async with pool.acquire() as conn, AppRoleTx(conn):
        rows = await conn.fetch("SELECT 1 FROM consent_record")
    assert rows == []


async def test_second_set_local_within_same_transaction_does_not_survive_commit(pool):
    """같은 트랜잭션 안에서 `app.tenant_id`를 두 번 다른 값으로 설정해도,
    트랜잭션이 끝나면(커밋이든 롤백이든) 둘 다 사라진다 — 마지막으로 설정한
    값이 다음 트랜잭션까지 이어지는 누수가 없는지를 확인한다."""
    tenant_a = await create_test_user(pool)
    tenant_b = await create_test_user(pool)

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(tenant_a)
            )
            await conn.execute(
                "SELECT set_config('app.tenant_id', $1, true)", str(tenant_b)
            )
            current = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
            assert current == str(tenant_b)

        after_commit = await conn.fetchval("SELECT current_setting('app.tenant_id', true)")
    assert after_commit in (None, "")
