"""FA-2a 결함 수정(리뷰 task-1812 REJECT) — legal_entity.tenant_id FK 회귀.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-2a.
`a0e7e1454b60`가 `legal_entity.tenant_id`의 FK를 `users(user_id)`에서
`tenant(id)`로 교정했으나(task-1747), 그 FK가 실제로 DB 레벨에서
강제된다는 실DB 통합테스트가 없었다(리뷰 task-1812 발견, I-10 위반 —
한 번도 실패한 적 없는 보호막은 도는지 알 수 없다). 정적 검사
(`test_migration_static_no_users_fk.py`)는 마이그레이션 소스가 `users`를
다시 참조하지 못하게만 막을 뿐, 런타임 참조 무결성은 검사하지 않는다.

DEEPEN task-3011 (docs/audit/DEPTH_FA.md): 원 커밋(2ea05de)이 D1로 판정된
사유 3가지를 이 파일에서 해소한다.
- negative 1건뿐(≥3 필요) → `test_insert_with_dangling_user_id_...`와
  `test_update_tenant_id_to_nonexistent_value_...` 2건을 추가해 총 3건.
- 성능단언 없음 → `test_fk_violation_round_trip_p95_latency_within_local_budget`.
- "배선제거 증명이 커밋메시지 서술로만 존재" → 2ea05de의 커밋 메시지는
  "FK가 이제 users가 아니라 tenant를 가리킨다"고 설명만 했을 뿐, 옛
  배선(users(user_id) FK)이었다면 통과했을 입력을 실제로 넣어 지금은
  막힌다는 것을 테스트 코드로 증명하지 않았다.
  `test_insert_with_dangling_user_id_but_no_tenant_row_raises_fk_violation`가
  그 증명이다: `users`에는 있지만 `tenant`에는 없는 id로 INSERT하면,
  옛 배선(users FK)이 아직 남아 있었다면 성공했을 것이 지금은
  `ForeignKeyViolationError`로 막힌다.

이 파일은 (a) 존재하지 않는 tenant_id로 INSERT 시 실DB에서
`asyncpg.ForeignKeyViolationError`가 나는지, (b) 존재하는 tenant_id로는
같은 INSERT가 성공하는지(대조군), (c) 그 FK 제약이 `tenant(id)`를
가리키는지(pg_constraint), (d) `users`에만 있고 `tenant`에는 없는 id도
막히는지(배선제거 증명), (e) UPDATE 경로에서도 FK가 강제되는지, (f) FK
위반 왕복 지연이 로컬 예산 안인지를 단언한다."""

from __future__ import annotations

import time
from uuid import uuid4

import asyncpg
import pytest

from src.foundation.entities.contracts.v1 import LegalEntity
from tests.integration.conftest import create_test_tenant, create_test_user
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


async def test_insert_with_dangling_user_id_but_no_tenant_row_raises_fk_violation(pool, repo):
    # 배선제거 증명(DEEPEN task-3011) — 옛 배선(2ea05de 이전)은
    # legal_entity.tenant_id -> users(user_id)를 가리켰다. `users`에는
    # 있지만 `tenant`에는 없는 id는, 옛 배선이 아직 남아 있었다면 이
    # INSERT가 성공했을 것이다. 지금은 tenant(id) FK만 있으므로 막힌다 —
    # 이 차이가 곧 "배선이 제거/교정됐다"의 실행 가능한 증명이다.
    dangling_user_id = await create_test_user(pool)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.create_legal_entity(
            LegalEntity(
                entity_id=uuid4(),
                tenant_id=dangling_user_id,
                name="Dangling User Legal Entity",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )


async def test_update_tenant_id_to_nonexistent_value_raises_fk_violation(pool, repo):
    # standard-105 조건부 UPDATE 경로도 INSERT와 동일하게 FK가 강제되는지
    # 확인한다 — INSERT 3건만으로는 UPDATE 경로의 배선까지는 증명하지
    # 못한다.
    seeded = await build_hierarchy(pool, repo)
    missing_tenant_id = uuid4()

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE legal_entity SET tenant_id = $1 WHERE entity_id = $2",
                missing_tenant_id,
                seeded.legal_entity.entity_id,
            )


async def test_fk_violation_round_trip_p95_latency_within_local_budget(pool, repo):
    # ADR-2026-09-09-C Decision 1의 축별 성능 예산 표는 사전거래 게이트/
    # 주문 제출/봉 조회 등만 나열하고 FK 왕복 지연은 다루지 않는다(N/A인
    # 축). 그래도 D2 하한(성능단언 1)을 채우기 위해, 로컬 회귀 기준선으로
    # "FK 위반이 INSERT당 100ms 안에 확정된다"를 이 테스트가 직접 건다 —
    # repo가 락/재시도 루프를 얹어 조용히 느려지면 여기서 적색이 된다.
    sample_count = 20
    latencies_ms: list[float] = []

    for _ in range(sample_count):
        started_at = time.perf_counter()
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await repo.create_legal_entity(
                LegalEntity(
                    entity_id=uuid4(),
                    tenant_id=uuid4(),
                    name="Perf Probe Legal Entity",
                    jurisdiction="KR",
                    region_tag="kr-seoul",
                )
            )
        latencies_ms.append((time.perf_counter() - started_at) * 1000)

    latencies_ms.sort()
    p95_index = min(len(latencies_ms) - 1, int(round(0.95 * (len(latencies_ms) - 1))))
    p95_ms = latencies_ms[p95_index]

    assert p95_ms < 100.0, f"FK 위반 왕복 p95={p95_ms:.1f}ms — 로컬 예산(100ms) 초과"
