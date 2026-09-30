"""PLT-26 DEEPEN(task-9230) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_migration_tenant_membership.py`가 이미 unique 제약(부분 인덱스)/
`tenant.kind` CHECK/`tenant_membership.role` CHECK/`subject_id` FK/backfill
치유/성능/게이트-적색 재현을 폭넓게 다루므로, 여기서는 `f4a6b8c0d2e4`의
DDL(`docs/specs/L4_platform_observability_tenancy_api_v1.0.md` §2) 중 그
파일이 실측하지 않는 세 공백만 메운다:

1. `tenant.state` CHECK(ACTIVE/SUSPENDED/DELETED) -- 기존 테스트는
   `tenant.kind` CHECK만 실측한다.
2. `tenant_membership.state` CHECK(ACTIVE/SUSPENDED/REVOKED) -- 기존
   테스트는 `role` CHECK만 실측한다.
3. `tenant_membership.tenant_id` FK(`tenant(id)`) -- 기존 테스트는
   `subject_id` FK(`users(user_id)`)만 실측한다.

추가로 실패주입 1건: 표준-105(조건부 UPDATE/FOR UPDATE/멱등키) 패턴을
따르는 호출자가 `tenant`+`tenant_membership` 삽입을 한 트랜잭션으로 묶었을
때, 두 번째(`tenant_membership`) 삽입이 CHECK 위반으로 실패하면 첫 번째
(`tenant`) 삽입도 함께 롤백되어 고아 `tenant` 행이 남지 않는지 검증한다 --
트랜잭션 밖에서 각각 커밋하면 이 원자성이 깨져 b8ac30eb4fe0이 치유하는
바로 그 "user는(여기서는 tenant는) 있는데 대응 행이 없는" 오염을 그대로
재현하게 된다.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from uuid import uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant, create_test_user


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    dsn = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    p = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
    yield p
    await p.close()


# ---------------------------------------------------------------------------
# Negative tests -- CHECK/FK 제약 (test_migration_tenant_membership.py가
# 다루지 않는 축)
# ---------------------------------------------------------------------------


async def test_tenant_state_check_constraint_rejects_invalid_state(
    pool: asyncpg.Pool,
) -> None:
    """negative -- `tenant.state`는 ACTIVE/SUSPENDED/DELETED 세 값만
    허용한다(f4a6b8c0d2e4 CHECK). 임의 문자열은 거부되어야 한다."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO tenant (id, kind, state) VALUES ($1, 'PERSONAL', 'ARCHIVED')",
                uuid4(),
            )


async def test_tenant_membership_state_check_constraint_rejects_invalid_state(
    pool: asyncpg.Pool,
) -> None:
    """negative -- `tenant_membership.state`는 ACTIVE/SUSPENDED/REVOKED
    세 값만 허용한다. 임의 문자열은 거부되어야 한다."""
    tenant_id = await create_test_tenant(pool, bootstrap_default_hierarchy_rows=False)
    subject_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role, state) "
                "VALUES ($1, $2, 'MEMBER', 'PENDING')",
                tenant_id,
                subject_id,
            )


async def test_tenant_membership_tenant_must_reference_existing_tenant(
    pool: asyncpg.Pool,
) -> None:
    """negative -- `tenant_membership.tenant_id`는 `tenant(id)` FK다.
    존재하지 않는 tenant_id는 ForeignKeyViolation으로 거부되어야 한다
    (기존 테스트는 `subject_id` FK만 실측한다)."""
    subject_id = await create_test_user(pool)

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO tenant_membership (tenant_id, subject_id, role) "
                "VALUES ($1, $2, 'MEMBER')",
                uuid4(),  # tenant 테이블에 없는 임의 id
                subject_id,
            )


# ---------------------------------------------------------------------------
# Failure injection -- 트랜잭션 원자성
# ---------------------------------------------------------------------------


async def test_transaction_rollback_on_membership_insert_failure_leaves_no_orphan_tenant(
    pool: asyncpg.Pool,
) -> None:
    """실패주입 -- 표준-105 패턴을 따르는 호출자가 `tenant`와
    `tenant_membership` 삽입을 같은 트랜잭션으로 묶었을 때, 두 번째 삽입이
    CHECK 위반으로 실패하면 첫 번째 삽입도 함께 롤백되어야 한다(fail-closed).
    각각을 별 트랜잭션/커넥션으로 커밋하면 이 원자성이 사라지고 b8ac30eb4fe0이
    치유 대상으로 삼는 것과 같은 모양의 고아 행(대응 membership 없는 tenant)이
    조용히 남는다 -- 이 테스트는 그 반대(트랜잭션 안이면 남지 않음)를
    직접 증명한다."""
    subject_id = await create_test_user(pool)
    tenant_id = uuid4()

    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO tenant (id, kind) VALUES ($1, 'ORGANIZATION')", tenant_id
                )
                # 의도적으로 잘못된 role -- CHECK 위반으로 트랜잭션 전체가 롤백돼야 한다.
                await conn.execute(
                    "INSERT INTO tenant_membership (tenant_id, subject_id, role) "
                    "VALUES ($1, $2, 'ROOT')",
                    tenant_id,
                    subject_id,
                )

    async with pool.acquire() as conn:
        orphan = await conn.fetchval("SELECT id FROM tenant WHERE id = $1", tenant_id)
    assert orphan is None, (
        "membership 삽입 실패 시 tenant 삽입도 함께 롤백되어야 한다 -- "
        "고아 tenant 행이 남으면 안 된다"
    )
