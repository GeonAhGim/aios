"""FA-0a batch A — tenant_id FK가 tenant(id)를 실제로 강제하는지 실DB로 확인.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md §9 FA-0a
task-1814 DoD(b). `tests/integration/foundation/entities/test_tenant_fk_enforced.py`
(FA-2a legal_entity)와 같은 형태 — (a) 존재하지 않는 tenant_id로 INSERT하면
`asyncpg.ForeignKeyViolationError`가 나는지, (b) 존재하는 tenant_id로는
같은 INSERT가 성공하는지(대조군), (c) 그 FK 제약이 실제로 `tenant(id)`를
가리키는지(pg_constraint)를 단언한다. 대표로 배치 A 9개 테이블 중
`account_connection`(NOT NULL)과 `foundation_audit_event`(NULLABLE, system
이벤트 예외가 있는 유일한 테이블)를 검증한다.
"""

from __future__ import annotations

import os
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def _fk_ref(pool: asyncpg.Pool, table: str, constraint: str) -> tuple[str, str] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT confrelid::regclass::text AS ref_table, a.attname AS ref_column
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.confrelid AND a.attnum = ANY(c.confkey)
            WHERE c.conrelid = $1::regclass AND c.contype = 'f' AND c.conname = $2
            """,
            table,
            constraint,
        )
    return (row["ref_table"], row["ref_column"]) if row is not None else None


async def test_account_connection_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "account_connection", "account_connection_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_account_connection_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO account_connection
                    (tenant_id, owner_subject_id, provider_code, opaque_account_ref,
                     capability_profile)
                VALUES ($1, $1, 'fake-broker', 'ACCT-orphan', ARRAY['READ_BALANCE'])
                """,
                missing_tenant_id,
            )


async def test_account_connection_insert_with_existing_tenant_id_succeeds(pool):
    tenant_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO account_connection
                (tenant_id, owner_subject_id, provider_code, opaque_account_ref,
                 capability_profile)
            VALUES ($1, $1, 'fake-broker', 'ACCT-control', ARRAY['READ_BALANCE'])
            RETURNING tenant_id
            """,
            tenant_id,
        )

    assert row["tenant_id"] == tenant_id


async def test_foundation_audit_event_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "foundation_audit_event", "foundation_audit_event_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_foundation_audit_event_insert_with_nonexistent_tenant_id_raises_fk_violation(
    pool,
):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO foundation_audit_event
                    (tenant_id, sequence_no, aggregate_type, aggregate_id, action, outcome,
                     trace_id, payload_hash, payload, event_hash)
                VALUES ($1, $2, 'test.aggregate', gen_random_uuid(), 'test.action',
                        'SUCCESS', gen_random_uuid(), 'hash', '{}'::jsonb, 'hash')
                """,
                missing_tenant_id,
                uuid4().int % 1_000_000_000,
            )


async def test_foundation_audit_event_null_tenant_id_still_allowed(pool):
    # NULL은 FK 체크 대상이 아니다(system 이벤트, 4453afe74725 §1) — FK를
    # tenant(id)로 옮긴 뒤에도 이 예외가 유지되는지 대조군으로 확인한다.
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO foundation_audit_event
                (tenant_id, sequence_no, aggregate_type, aggregate_id, action, outcome,
                 trace_id, payload_hash, payload, event_hash)
            VALUES (NULL, $1, 'test.aggregate', gen_random_uuid(), 'test.action',
                    'SUCCESS', gen_random_uuid(), 'hash', '{}'::jsonb, 'hash')
            RETURNING tenant_id
            """,
            uuid4().int % 1_000_000_000,
        )

    assert row["tenant_id"] is None
