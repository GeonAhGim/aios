"""EO-02 DEEPEN(task-9244) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_postgres_lease_repository.py`가 이미 동시성/펜싱토큰/배치 왕복수/FK
경계의 주된 negative 증거를 갖고 있으므로, 여기서는 그 파일이 다루지 않은
공백만 메운다:

1. `release_all`이 아무 리스도 갖지 않은 owner에 대해 예외 없이 0을 반환하는지
   (기존 테스트는 항상 리스를 먼저 획득한 owner만 release한다).
2. 배치 안에 중복된 "존재하지 않는" execution_id가 섞여도 dedup 이후에도
   여전히 FK 위반으로 배치 전체가 실패하는지(중복 제거와 FK 검증의 상호작용은
   기존 테스트가 각각 따로만 다룬다).
3. `ttl_seconds`에 숫자로 캐스팅 불가능한 값을 넘기면 조용히 성공하는 대신
   즉시 실패하는지(fail-closed) -- 호출자 계약 위반을 조용히 삼키지 않는다.
4. `acquire_or_renew_many`/`release_all`이 asyncpg 자체가 아닌 예상치 못한
   의존성 예외(드라이버/네트워크 계층)를 삼키지 않고 그대로 전파하는지.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from src.foundation.execution_ownership.adapters.postgres_repository import (
    PostgresExecutionLeaseRepository,
)


def _owner_id() -> str:
    return f"owner-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_release_all_returns_zero_when_owner_holds_no_leases(pool: asyncpg.Pool):
    """한 번도 리스를 획득한 적 없는 owner에 대한 release_all은 예외 없이
    0을 반환해야 한다 -- 기존 테스트는 항상 리스를 먼저 획득한 owner만
    release한다."""
    repo = PostgresExecutionLeaseRepository(pool)

    released = await repo.release_all(_owner_id())

    assert released == 0


async def test_duplicate_unknown_execution_id_still_raises_fk_violation(
    pool: asyncpg.Pool, execution_id: int
):
    """존재하지 않는 execution_id가 배치 안에서 중복돼도 dedup 이후 여전히
    FK 위반으로 배치 전체가 실패해야 한다 -- dedup(중복 제거)과 FK 검증은
    기존 테스트에서 각각 따로만(정상 id 중복 / 미지 id 단독) 다뤄진다."""
    repo = PostgresExecutionLeaseRepository(pool)
    unknown_id = 10**12

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await repo.acquire_or_renew_many(
            [execution_id, unknown_id, unknown_id],
            owner_id=_owner_id(),
            ttl_seconds=30,
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM execution_leases WHERE execution_id = $1", execution_id
        )
    assert row is None


async def test_acquire_or_renew_many_rejects_non_numeric_ttl_seconds(
    pool: asyncpg.Pool, execution_id: int
):
    """`ttl_seconds`에 숫자로 캐스팅 불가능한 문자열을 넘기면 조용히 리스를
    내주는 대신 즉시 실패해야 한다 -- 호출자 계약(§3.2 ttl_seconds는 초 단위
    숫자) 위반을 fail-closed로 거부한다."""
    repo = PostgresExecutionLeaseRepository(pool)
    # dict[str, Any] 언패킹은 mypy가 object로 넓혀 잡아 `type: ignore` 없이도
    # 잘못된 타입의 인자를 만들어 낼 수 있다(D2/D3 negative test와
    # type_ignore 예산 제약이 충돌할 때의 표준 우회, CLAUDE.md 6.14).
    bad_kwargs: dict[str, object] = {
        "owner_id": _owner_id(),
        "ttl_seconds": "not-a-number",
    }

    with pytest.raises((asyncpg.PostgresError, ValueError, TypeError)):
        await repo.acquire_or_renew_many([execution_id], **bad_kwargs)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM execution_leases WHERE execution_id = $1", execution_id
        )
    assert row is None


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_acquire_or_renew_many_propagates_unexpected_dependency_error(
    pool: asyncpg.Pool, execution_id: int, monkeypatch: pytest.MonkeyPatch
):
    """`acquire_or_renew_many`가 잡는 예외는 없다 -- 드라이버/네트워크 계층에서
    터지는 예상치 못한 예외(예: 커넥션 끊김)는 조용히 삼켜지지 않고 그대로
    호출자에게 전파돼야 한다(fail-closed)."""
    repo = PostgresExecutionLeaseRepository(pool)

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object) -> None:
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(asyncpg.Connection, "fetch", _boom)

    with pytest.raises(RuntimeError, match="dependency exploded"):
        await repo.acquire_or_renew_many([execution_id], owner_id=_owner_id(), ttl_seconds=30)


async def test_release_all_propagates_unexpected_dependency_error(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`release_all`도 동일하게 예상치 못한 의존성 예외를 삼키지 않고 그대로
    전파해야 한다."""
    repo = PostgresExecutionLeaseRepository(pool)

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object) -> str:
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(asyncpg.Connection, "execute", _boom)

    with pytest.raises(RuntimeError, match="dependency exploded"):
        await repo.release_all(_owner_id())
