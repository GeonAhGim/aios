"""LA-25 — pos_borrow_position 스키마 실DB 검증(ddbd3a33bf30).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 LA-25,
task-1752 decision. (a) tenant_id FK가 실제로 `tenant(id)`를 가리키는지,
(b) 존재하지 않는 tenant_id INSERT가 FK 위반으로 거부되는지(negative),
(c) RLS `tenant_isolation`이 교차 테넌트 SELECT를 0행으로 막는지를
`tests/integration/foundation/test_tenant_fk_enforced_batch_a.py`·
`tests/integration/core/db/test_rls_foundation_fa0a_batch_a.py`와
동일한 형태로 단언한다.

task-3000(DEEPEN, docs/audit/DEPTH_LA_LB_LC.md task-1752 행): 원 리프가
tenant_id FK 거부·RLS SELECT 교차증명만 갖췄을 뿐 (1) FK/RLS 이외의 실
제약(CHECK/UNIQUE/두 번째 FK) failure-injection, (2) 수치 성능 단언,
(3) 게이트 적색 재현이 없어 D3 축 하한(D2 4요건) 미달로 판정됐다. 아래
세 그룹을 보강한다:
- CHECK/UNIQUE/portfolio FK 위반(FK/RLS 외 실 제약 집행) 5건
- `test_pos_borrow_position_batch_insert_meets_latency_budget`(수치 성능
  단언)
- `test_pos_borrow_position_insert_for_other_tenant_is_rejected`(게이트
  적색 재현 — `test_rls_foundation.py::test_insert_for_other_tenant_is_rejected`
  와 동일 패턴으로 세션 tenant_id와 다른 tenant_id를 강제한 INSERT의
  WITH CHECK 거부를 실증. 기존 4케이스는 모두 세션과 일치하는 정직한
  tenant_id만 다뤄 이 방어선을 밟지 않았다)
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import asyncpg
import pytest

from tests.integration.conftest import create_test_tenant
from tests.integration.core.db.conftest import AppRoleTx
from tests.support.entities_seed import bootstrap_default_portfolio


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


async def _seed_borrow_position(pool: asyncpg.Pool, tenant_id: UUID, *, position_key: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pos_borrow_position
                (position_key, tenant_id, short_quantity, supply_rate, currency)
            VALUES ($1, $2, 100, 0.03, 'USDT')
            """,
            position_key,
            tenant_id,
        )


async def test_pos_borrow_position_tenant_id_fk_references_tenant_table(pool):
    ref = await _fk_ref(pool, "pos_borrow_position", "pos_borrow_position_tenant_id_fkey")
    assert ref == ("tenant", "id")


async def test_pos_borrow_position_insert_with_nonexistent_tenant_id_raises_fk_violation(pool):
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, 0.03, 'USDT')
                """,
                f"BORROW-ORPHAN-{uuid4().hex}",
                missing_tenant_id,
            )


async def test_pos_borrow_position_insert_with_existing_tenant_id_succeeds(pool):
    tenant_id = await create_test_tenant(pool)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO pos_borrow_position
                (position_key, tenant_id, short_quantity, supply_rate, currency)
            VALUES ($1, $2, 100, 0.03, 'USDT')
            RETURNING tenant_id
            """,
            f"BORROW-CONTROL-{uuid4().hex}",
            tenant_id,
        )

    assert row["tenant_id"] == tenant_id


async def test_pos_borrow_position_cross_tenant_select_returns_zero_rows(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_borrow_position(pool, tenant_a, position_key=f"BORROW-A-{uuid4().hex}")
    await _seed_borrow_position(pool, tenant_b, position_key=f"BORROW-B-{uuid4().hex}")

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        rows = await conn.fetch("SELECT tenant_id FROM pos_borrow_position")

    assert rows
    assert {r["tenant_id"] for r in rows} == {tenant_a}


async def test_pos_borrow_position_select_bound_to_other_tenant_excludes_all(pool):
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    await _seed_borrow_position(pool, tenant_a, position_key=f"BORROW-C-{uuid4().hex}")

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_b):
        rows = await conn.fetch(
            "SELECT tenant_id FROM pos_borrow_position WHERE tenant_id = $1", tenant_a
        )

    assert rows == []


# --- FK/RLS 이외의 실 제약 집행 (failure-injection) ------------------------


async def test_pos_borrow_position_insert_with_zero_short_quantity_violates_check(pool):
    tenant_id = await create_test_tenant(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 0, 0.03, 'USDT')
                """,
                f"BORROW-NONPOS-QTY-{uuid4().hex}",
                tenant_id,
            )


async def test_pos_borrow_position_insert_with_negative_supply_rate_violates_check(pool):
    tenant_id = await create_test_tenant(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, -0.01, 'USDT')
                """,
                f"BORROW-NEG-RATE-{uuid4().hex}",
                tenant_id,
            )


async def test_pos_borrow_position_insert_with_invalid_currency_violates_check(pool):
    tenant_id = await create_test_tenant(pool)

    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, 0.03, 'XXX')
                """,
                f"BORROW-BAD-CCY-{uuid4().hex}",
                tenant_id,
            )


async def test_pos_borrow_position_insert_with_nonexistent_portfolio_id_raises_fk_violation(pool):
    """`portfolio_id`는 `tenant_id`와 별도의 FK다 — 존재하지 않는
    portfolio_id는 tenant_id FK 검증을 통과한 뒤에도 이 두 번째 제약에서
    거부돼야 한다(FK/RLS 이외의 실 제약이 아니라 두 번째 FK 자체지만,
    첫 FK 통과분만 다룬 기존 커버리지의 공백을 메운다)."""
    tenant_id = await create_test_tenant(pool)
    missing_portfolio_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, portfolio_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, $3, 100, 0.03, 'USDT')
                """,
                f"BORROW-ORPHAN-PORTFOLIO-{uuid4().hex}",
                tenant_id,
                missing_portfolio_id,
            )


async def test_pos_borrow_position_duplicate_position_key_raises_unique_violation(pool):
    tenant_id = await create_test_tenant(pool)
    position_key = f"BORROW-DUP-{uuid4().hex}"
    await _seed_borrow_position(pool, tenant_id, position_key=position_key)

    with pytest.raises(asyncpg.UniqueViolationError):
        await _seed_borrow_position(pool, tenant_id, position_key=position_key)


async def test_pos_borrow_position_insert_with_valid_portfolio_id_succeeds(pool):
    """양성 대조군 — 실제 존재하는 portfolio_id는 두 번째 FK를 통과한다."""
    tenant_id = await create_test_tenant(pool)
    portfolio_id = await bootstrap_default_portfolio(pool, tenant_id)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO pos_borrow_position
                (position_key, tenant_id, portfolio_id, short_quantity, supply_rate, currency)
            VALUES ($1, $2, $3, 100, 0.03, 'USDT')
            RETURNING portfolio_id
            """,
            f"BORROW-PORTFOLIO-OK-{uuid4().hex}",
            tenant_id,
            portfolio_id,
        )

    assert row["portfolio_id"] == portfolio_id


# --- 게이트 적색 재현 -------------------------------------------------------


async def test_pos_borrow_position_insert_for_other_tenant_is_rejected(pool):
    """`test_rls_foundation.py::test_insert_for_other_tenant_is_rejected`와
    동일 패턴 — 세션이 `app.tenant_id`로 tenant_a에 묶여 있는데 INSERT
    문에 다른 tenant(tenant_b)의 id를 강제로 밀어 넣으면(위조/오배선된
    tenant_id), `tenant_isolation` 정책의 `WITH CHECK`가 이를 그대로
    통과시키지 않고 fail-closed로 거부해야 한다 — RLS가 SELECT뿐 아니라
    쓰기 경로에서도 실제로 게이트를 적색으로 뒤집어 막는지의 증명이다.
    기존 4개 RLS 케이스는 모두 세션과 일치하는 정직한 tenant_id만 다뤄
    이 방어선을 밟지 않았다."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)

    async with pool.acquire() as conn, AppRoleTx(conn, tenant_id=tenant_a):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, 0.03, 'USDT')
                """,
                f"BORROW-FORGED-TENANT-{uuid4().hex}",
                tenant_b,
            )


# --- 수치 성능 단언 ---------------------------------------------------------


@pytest.mark.perf
async def test_pos_borrow_position_batch_insert_meets_latency_budget(pool):
    """50건의 `pos_borrow_position` 순차 INSERT가 3.0초 예산 내에 끝나야
    한다(LC-15a `test_schedule_payouts_batch_meets_latency_budget`,
    task-2961과 동일 판단 — 공유 로컬 Postgres 지연 변동을 흡수할 만큼
    넉넉하되 무한정은 아닌 회귀 감시 상한). 이 leaf에 저장소 어댑터가
    아직 없어 배치 write 경로는 이 원시 INSERT 반복이 유일한 대리
    측정치다."""
    tenant_id = await create_test_tenant(pool)
    batch_size = 50

    start = time.monotonic()
    async with pool.acquire() as conn:
        for i in range(batch_size):
            await conn.execute(
                """
                INSERT INTO pos_borrow_position
                    (position_key, tenant_id, short_quantity, supply_rate, currency)
                VALUES ($1, $2, 100, 0.03, 'USDT')
                """,
                f"BORROW-PERF-{uuid4().hex}-{i}",
                tenant_id,
            )
    elapsed = time.monotonic() - start

    assert elapsed < 3.0, f"{batch_size}건 순차 INSERT가 예산(3.0s)을 초과: {elapsed:.3f}s"
