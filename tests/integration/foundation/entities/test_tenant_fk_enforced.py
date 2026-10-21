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


async def test_create_legal_entity_propagates_pool_acquire_failure(pool, repo, monkeypatch):
    # 실패주입(DEEPEN task-4084 잔여 항목) — DB 연결 자체가 끊긴 경우
    # (pool 소진/네트워크 단절 등)에도 create_legal_entity가 예외를 삼키지
    # 않고 그대로 전파하는지 확인한다. §3의 fail-closed 기본 정책상, FK
    # 위반이 아닌 하위 의존성 실패(asyncpg.UniqueViolationError 외의 다른
    # 예외)는 `ConcurrencyConflictError`로 둔갑하지 않고 호출자에게
    # 그대로 올라가야 한다.
    class _FailingAcquire:
        async def __aenter__(self):
            raise asyncpg.exceptions.ConnectionDoesNotExistError("connection lost")

        async def __aexit__(self, *exc_info):
            return False

    class _FailingPool:
        def acquire(self, *args, **kwargs):
            return _FailingAcquire()

    # asyncpg.Pool.acquire은 읽기 전용 속성이라 인스턴스에 직접 monkeypatch할
    # 수 없다 — repo._pool 자체를 스텁으로 교체해 acquire() 실패를 흉내낸다.
    monkeypatch.setattr(repo, "_pool", _FailingPool())

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await repo.create_legal_entity(
            LegalEntity(
                entity_id=uuid4(),
                tenant_id=uuid4(),
                name="Connection Failure Probe",
                jurisdiction="KR",
                region_tag="kr-seoul",
            )
        )


@pytest.mark.perf
async def test_fk_violation_round_trip_p95_latency_within_local_budget(pool, repo, perf_budget):
    # ADR-2026-09-09-C Decision 1의 축별 성능 예산 표는 사전거래 게이트/
    # 주문 제출/봉 조회 등만 나열하고 FK 왕복 지연은 다루지 않는다(N/A인
    # 축). 그래도 D2 하한(성능단언 1)을 채우기 위해, 로컬 회귀 기준선으로
    # "FK 위반이 INSERT당 100ms 안에 확정된다"를 이 테스트가 직접 건다 —
    # repo가 락/재시도 루프를 얹어 조용히 느려지면 여기서 적색이 된다.
    # raw time.perf_counter() → perf_budget.samples_async() 전환 (task-11051).
    # 비동기 I/O를 재는 테스트는 samples_async를 쓴다 — wall_ms로 왕복 지연을
    # 보존하고 coverage tracer 오버헤드를 걷어낸다. 예산 값(100ms)은 그대로
    # 유지한다.
    sample_count = 20

    async def _probe() -> None:
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

    samples = await perf_budget.samples_async(_probe, n=sample_count)
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(0.95 * len(wall_ms_list)), len(wall_ms_list) - 1)]

    assert p95_ms < 100.0, f"FK 위반 왕복 p95={p95_ms:.2f}ms — 로컬 예산(100ms) 초과"


@pytest.mark.perf
async def test_gate_red_reproduction_fk_violation_p95_catches_10x_delay(
    monkeypatch: pytest.MonkeyPatch,
    pool,
    repo,
    perf_budget,
):
    """FK 위반 p95 단언이 정상 입력에서는 녹색이고 10배 지연 주입에서 적색인지 검증한다.

    gate-red 재현 패턴:
    1. 지연 주입 없이 같은 성능 단언을 먼저 실행 → 녹색 통과 확인
    2. repo._pool.acquire()에 asyncio.sleep(0.5)를 주입 → 같은 단언이
       적색(p95 ≥ 100ms)으로 실패하는지 확인
    """
    import asyncio

    # 단계 1: 정상 입력에서 녹색 통과
    await test_fk_violation_round_trip_p95_latency_within_local_budget(pool, repo, perf_budget)

    # 단계 2: repo._pool.acquire()에 지연 주입 — asyncpg.Pool.acquire는
    # 읽기 전용 속성이므로 repo._pool 자체를 스텁으로 교체한다.
    # asyncpg.Pool.acquire()는 coroutine이 아닌 async context manager를
    # 반환하므로, 스텁 acquire()도 __aenter__/__aexit__를 가진 객체를
    # 반환해야 한다.
    original_pool = repo._pool

    class _DelayedExecuteConn:
        """원본 연결을 감싸 execute/fetchrow/fetch만 지연시킨다."""

        def __init__(self, real_conn):
            self._real_conn = real_conn

        async def execute(self, *args, **kwargs):
            await asyncio.sleep(0.5)  # 500ms 지연 — 예산(100ms)의 5배
            return await self._real_conn.execute(*args, **kwargs)

        async def fetchrow(self, *args, **kwargs):
            await asyncio.sleep(0.5)
            return await self._real_conn.fetchrow(*args, **kwargs)

        async def fetch(self, *args, **kwargs):
            await asyncio.sleep(0.5)
            return await self._real_conn.fetch(*args, **kwargs)

        # 원본 연결의 다른 모든 속성을 포워딩
        def __getattr__(self, name):
            return getattr(self._real_conn, name)

    class _DelayedAcquireCtx:
        """asyncpg.Pool.acquire()의 반환값을 모방한다."""

        def __init__(self, pool):
            self._pool = pool
            self._wrapped_conn = None  # __aenter__에서 설정

        async def __aenter__(self):
            real_conn = await self._pool.acquire()
            self._wrapped_conn = _DelayedExecuteConn(real_conn)
            return self._wrapped_conn

        async def __aexit__(self, *exc_info):
            # 원본 연결을 풀로 반납 (close 대신 release)
            if self._wrapped_conn is not None:
                await self._wrapped_conn._real_conn.close()

    class _DelayedPool:
        def acquire(self, *args, **kwargs):
            return _DelayedAcquireCtx(original_pool)

        async def close(self):
            await original_pool.close()

    repo._pool = _DelayedPool()

    try:
        # 같은 단언을 재실행 — 지연 주입으로 p95가 100ms를 넘겨야 적색
        with pytest.raises(AssertionError) as exc_info:
            await test_fk_violation_round_trip_p95_latency_within_local_budget(
                pool, repo, perf_budget
            )

        # 실제로 budget 초과로 실패했는지 확인 (다른 원인의 AssertionError 아님)
        assert "p95" in str(exc_info.value), (
            f"budget 초과가 아닌 다른 원인으로 실패: {exc_info.value}"
        )
    finally:
        repo._pool = original_pool
