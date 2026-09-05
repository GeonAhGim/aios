"""EO-02 — PostgresExecutionLeaseRepository 실DB 통합테스트.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§8 통합 — 서로 다른 owner_id 동시 acquire는 정확히 1개만 성공, 만료 후
재획득은 fencing_token +1, 동일 소유자 갱신은 토큰 불변. §7 — 배치는
1회 왕복으로 끝나야 한다."""
from __future__ import annotations

import asyncio
import uuid
from unittest import mock

import asyncpg
import pytest

from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)
from tests.integration.conftest import create_test_user
from tests.integration.foundation.execution_ownership.conftest import create_execution


def _owner_id() -> str:
    # execution_leases는 이 테스트 세션 전체가 공유하는 테이블이라(트랜잭션
    # 롤백 격리 없음) 매 테스트마다 고유한 owner_id를 써야 다른 테스트가
    # 남긴 행과 release_all/조회 결과가 섞이지 않는다.
    return f"owner-{uuid.uuid4().hex[:8]}"


async def _fetch_lease(pool: asyncpg.Pool, execution_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT owner_id, fencing_token FROM execution_leases WHERE execution_id = $1",
            execution_id,
        )


async def test_concurrent_acquire_exactly_one_winner(pool, execution_id):
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a, owner_b = _owner_id(), _owner_id()
    results = await asyncio.gather(
        repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=30),
        repo.acquire_or_renew_many([execution_id], owner_id=owner_b, ttl_seconds=30),
    )
    winners = [r for r in results if execution_id in r]
    assert len(winners) == 1


async def test_expired_lease_reacquired_with_incremented_fencing_token(pool, execution_id):
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a, owner_b = _owner_id(), _owner_id()
    first = await repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=-1)
    assert first == {execution_id}

    second = await repo.acquire_or_renew_many([execution_id], owner_id=owner_b, ttl_seconds=30)
    assert second == {execution_id}

    row = await _fetch_lease(pool, execution_id)
    assert row["owner_id"] == owner_b
    assert row["fencing_token"] == 1


async def test_same_owner_renewal_keeps_fencing_token(pool, execution_id):
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a = _owner_id()
    await repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=30)
    await repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=30)

    row = await _fetch_lease(pool, execution_id)
    assert row["owner_id"] == owner_a
    assert row["fencing_token"] == 0


async def test_other_owner_denied_while_lease_valid(pool, execution_id):
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a, owner_b = _owner_id(), _owner_id()
    await repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=30)

    denied = await repo.acquire_or_renew_many([execution_id], owner_id=owner_b, ttl_seconds=30)
    assert denied == set()

    row = await _fetch_lease(pool, execution_id)
    assert row["owner_id"] == owner_a
    assert row["fencing_token"] == 0


async def test_empty_execution_ids_returns_empty_without_query(pool):
    repo = PostgresExecutionLeaseRepository(pool)
    with mock.patch.object(asyncpg.Pool, "acquire") as acquire_mock:
        result = await repo.acquire_or_renew_many([], owner_id=_owner_id(), ttl_seconds=30)
    assert result == set()
    acquire_mock.assert_not_called()


async def test_batch_acquire_is_single_round_trip(pool):
    user_id = await create_test_user(pool)
    exec_a = await create_execution(pool, user_id)
    exec_b = await create_execution(pool, user_id)
    repo = PostgresExecutionLeaseRepository(pool)

    call_count = 0
    original_fetch = asyncpg.Connection.fetch

    async def counting_fetch(self, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        return await original_fetch(self, *args, **kwargs)

    with mock.patch.object(asyncpg.Connection, "fetch", counting_fetch):
        acquired = await repo.acquire_or_renew_many(
            [exec_a, exec_b], owner_id=_owner_id(), ttl_seconds=30
        )

    assert call_count == 1
    assert acquired == {exec_a, exec_b}


async def test_release_all_deletes_only_owned_leases(pool, execution_id):
    user_id = await create_test_user(pool)
    other_execution_id = await create_execution(pool, user_id)
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a, owner_b = _owner_id(), _owner_id()
    await repo.acquire_or_renew_many([execution_id], owner_id=owner_a, ttl_seconds=30)
    await repo.acquire_or_renew_many([other_execution_id], owner_id=owner_b, ttl_seconds=30)

    released = await repo.release_all(owner_a)
    assert released == 1

    assert await _fetch_lease(pool, execution_id) is None
    assert await _fetch_lease(pool, other_execution_id) is not None


async def test_duplicate_execution_ids_in_one_batch_do_not_abort_batch(pool):
    # 같은 id가 두 번 들어오면 Postgres가 "cannot affect row a second time"
    # (CardinalityViolationError)로 배치 전체를 거부한다 — 어댑터가 바인딩 전
    # 중복을 제거해 다른 execution까지 tick 못 하는 사고를 막아야 한다.
    user_id = await create_test_user(pool)
    exec_a = await create_execution(pool, user_id)
    exec_b = await create_execution(pool, user_id)
    repo = PostgresExecutionLeaseRepository(pool)

    acquired = await repo.acquire_or_renew_many(
        [exec_a, exec_a, exec_b, exec_a], owner_id=_owner_id(), ttl_seconds=30
    )

    assert acquired == {exec_a, exec_b}
    assert (await _fetch_lease(pool, exec_a))["fencing_token"] == 0


async def test_mixed_batch_returns_only_acquirable_ids(pool):
    # 배치 일부가 타 소유자 점유 중이면 예외 없이 그 id만 반환 집합에서 빠진다
    # (§3.2 "갱신 실패는 반환 집합에서 빠지는 형태로만 신호").
    user_id = await create_test_user(pool)
    held = await create_execution(pool, user_id)
    free = await create_execution(pool, user_id)
    repo = PostgresExecutionLeaseRepository(pool)
    owner_a, owner_b = _owner_id(), _owner_id()
    assert await repo.acquire_or_renew_many([held], owner_id=owner_a, ttl_seconds=30) == {held}

    acquired = await repo.acquire_or_renew_many([held, free], owner_id=owner_b, ttl_seconds=30)

    assert acquired == {free}
    assert (await _fetch_lease(pool, held))["owner_id"] == owner_a


async def test_unknown_execution_id_raises_fk_violation_for_whole_batch(pool, execution_id):
    # 계약 경계 기록: FK 위반은 걸러내지 않는다(§5.1 SQL 그대로). 호출자(EO-03)는
    # strategy_executions에서 읽은 id만 넘겨야 하며, 그렇지 않으면 배치 전체가 실패한다.
    repo = PostgresExecutionLeaseRepository(pool)
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.acquire_or_renew_many(
            [execution_id, 10**12], owner_id=_owner_id(), ttl_seconds=30
        )
    assert await _fetch_lease(pool, execution_id) is None
