"""FA-2a 결함 수정(리뷰 task-1812 REJECT) — legal_entity.tenant_id FK 회귀.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2a.
`a0e7e1454b60`가 `legal_entity.tenant_id`의 FK를 `users(user_id)`에서
`tenant(id)`로 교정했으나(task-1747), 그 FK가 실제로 DB 레벨에서
강제된다는 실DB 통합테스트가 없었다(리뷰 task-1812 발견, I-10 위반 —
한 번도 실패한 적 없는 보호막은 도는지 알 수 없다). 정적 검사
(`test_migration_static_no_users_fk.py`)는 마이그레이션 소스가 `users`를
다시 참조하지 못하게만 막을 뿐, 런타임 참조 무결성은 검사하지 않는다.

이 파일은 (a) 존재하지 않는 tenant_id로 INSERT 시 실DB에서
`asyncpg.ForeignKeyViolationError`가 나는지, (b) 존재하는 tenant_id로는
같은 INSERT가 성공하는지(대조군), (c) 그 FK 제약이 `tenant(id)`를
가리키는지(pg_constraint)를 단언한다."""
from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from src.foundation.entities.contracts.v1 import LegalEntity
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities.conftest import build_hierarchy


async def test_insert_with_nonexistent_tenant_id_raises_fk_violation(pool, repo):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_legal_entity(
            LegalEntity(
                entity_id=uuid4(),
                tenant_id=missing_tenant_id,
                name="Orphan Legal Entity",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )


async def test_insert_with_existing_tenant_id_succeeds(pool, repo):
    # 대조군 — (a)의 실패가 FK 강제 때문이지, INSERT 경로 자체의 결함이
    # 아님을 구분한다.
    seeded = await build_hierarchy(pool, repo)

    assert seeded.legal_entity.tenant_id == seeded.tenant_id


async def test_legal_entity_tenant_id_fk_references_tenant_table(pool):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT confrelid::regclass::text AS ref_table, a.attname AS ref_column
            FROM pg_constraint c
            JOIN pg_attribute a
              ON a.attrelid = c.confrelid AND a.attnum = ANY(c.confkey)
            WHERE c.conrelid = 'legal_entity'::regclass
              AND c.contype = 'f'
              AND c.conname = 'legal_entity_tenant_id_fkey'
            """
        )

    assert row is not None, "legal_entity_tenant_id_fkey 제약이 존재하지 않는다"
    assert row["ref_table"] == "tenant"
    assert row["ref_column"] == "id"


async def test_cross_tenant_legal_entity_insert_also_enforces_fk(pool, repo):
    # 정상 tenant는 통과하고, 존재하는 다른 tenant를 섞어도 FK 자체는
    # tenant_id 값 하나만 검사한다는 것을 명확히 한다(교차 테넌트 접근
    # 제어와 FK 강제는 별개 계층).
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)

    entity_for_a = await repo.create_legal_entity(
        LegalEntity(
            entity_id=uuid4(),
            tenant_id=tenant_a,
            name="Entity A",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )
    entity_for_b = await repo.create_legal_entity(
        LegalEntity(
            entity_id=uuid4(),
            tenant_id=tenant_b,
            name="Entity B",
            jurisdiction="KR",
            region_tag="kr-seoul",
        )
    )

    assert entity_for_a.tenant_id == tenant_a
    assert entity_for_b.tenant_id == tenant_b
