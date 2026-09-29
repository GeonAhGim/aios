"""DEEPEN(task-7706, 원 리프 task-5487): `conftest.py`의 픽스처 헬퍼
(`create_pos_account`/`open_position`/`_asyncpg_dsn`)가 불변식 위반 입력을
명시적으로 거부하는지, 그리고 의존성(커넥션) 실패 시 조용히 삼키지 않고
그대로 전파(fail-closed)하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A).
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from uuid import UUID

import asyncpg
import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    _asyncpg_dsn,
    create_pos_account,
    open_position,
)


class _InjectedConnFailure(RuntimeError):
    pass


class _FailingConn:
    async def fetchval(self, *args: object, **kwargs: object) -> UUID:
        raise _InjectedConnFailure("injected connection failure in fetchval")


class _FailingAcquireCtx:
    async def __aenter__(self) -> _FailingConn:
        return _FailingConn()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingPool:
    """DB 없이도 실행 가능한 의존성 실패 주입용 이중체 -- 실제 asyncpg.Pool을
    대체하지 않고 `acquire()`만 흉내 낸다."""

    def acquire(self) -> _FailingAcquireCtx:
        return _FailingAcquireCtx()


async def test_create_pos_account_propagates_connection_failure() -> None:
    """실패주입: `pool.acquire()` 하위 커넥션의 `fetchval`이 예외를 던지면
    `create_pos_account`는 이를 삼키지 않고 그대로 전파해야 한다(fail-closed)."""
    with pytest.raises(_InjectedConnFailure, match="injected connection failure"):
        await create_pos_account(_FailingPool(), uuid.uuid4())


async def test_asyncpg_dsn_raises_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative: `DATABASE_URL`이 비어 있으면(설정 누락) 조용히 빈 DSN을
    만들어내지 말고 명시적으로 실패해야 한다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


async def test_create_pos_account_rejects_invalid_base_currency(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.base_currency`는 `Currency` enum 값만 허용하는
    DB CHECK 제약을 갖는다(마이그레이션 `4a1d0c0de004`) -- 임의 문자열은
    거부되어야 한다(게이트-적색 재현: 스키마가 이 방어를 갖지 않으면 조용히
    쓰레기 통화 코드가 저장된다)."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.fetchval(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
                "VALUES ($1, $2, $3, $4) RETURNING account_id",
                tenant_id,
                "TESTVENUE",
                "XXX",
                "FIFO",
            )


async def test_create_pos_account_rejects_invalid_cost_method(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.cost_method`도 `CostMethod` enum 값만 허용하는
    CHECK 제약이 있다 -- 임의 문자열은 거부되어야 한다."""
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(asyncpg.CheckViolationError):
        async with pool.acquire() as conn:
            await conn.fetchval(
                "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
                "VALUES ($1, $2, $3, $4) RETURNING account_id",
                tenant_id,
                "TESTVENUE",
                "KRW",
                "NOT_A_COST_METHOD",
            )


async def test_create_pos_account_rejects_unknown_tenant_id(pool: asyncpg.Pool) -> None:
    """negative: `pos_account.tenant_id`는 `users(user_id)` FK다 -- 존재하지
    않는 tenant로는 계정을 만들 수 없다."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_pos_account(pool, uuid.uuid4())


async def test_open_position_rejects_duplicate_first_creation_call(pool: asyncpg.Pool) -> None:
    """negative: `open_position`이 같은 `position_key`로 두 번 호출되면(둘 다
    `expected_seq=0`인 최초 생성 경로) 두 번째 호출은 이미 존재하는 행을
    최초 생성으로 오인하고 조용히 덮어써서는 안 된다 -- 실제로는
    `ConcurrencyConflictError`로 거부되어야 한다(task-3863 회귀 방지와 같은
    보호가 이 헬퍼를 통해서도 유지되는지 확인)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    from src.foundation.entities.domain.defaults import default_portfolio_id
    from src.foundation.positions.domain.position_key import PositionKey

    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )

    first = await open_position(
        pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
    )
    assert first.quantity == Decimal("0")

    with pytest.raises(ConcurrencyConflictError):
        await open_position(
            pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key
        )


@pytest.mark.perf
async def test_open_position_round_trip_stays_within_local_budget(pool: asyncpg.Pool) -> None:
    """성능 단언(D2): 기준 왕복 비용(pool.acquire + SELECT 1, n=20, 워밍업
    3회 버림)을 이 환경에서 직접 재고, `open_position`의 절대 시간이 그
    기준의 12배(연속 DB 왕복 여유, `test_perf_journal_append.py`와 같은
    정규화 방식) 이내인지 단언한다 -- 절대 ms 임계는 실행환경마다 흔들려
    회귀 게이트로 못 쓴다."""
    samples: list[float] = []
    for _ in range(23):
        start = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        samples.append(time.perf_counter() - start)
    baseline_rt = sorted(samples[3:])[-1]  # 워밍업 3회 버리고 최댓값(보수적 rt)

    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    from src.foundation.entities.domain.defaults import default_portfolio_id
    from src.foundation.positions.domain.position_key import PositionKey

    position_key = str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid.uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=default_portfolio_id(tenant_id),
        )
    )

    start = time.perf_counter()
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    elapsed = time.perf_counter() - start

    budget = max(0.030, 12 * baseline_rt)
    assert elapsed < budget, (
        f"open_position took {elapsed * 1000:.1f}ms, budget {budget * 1000:.1f}ms "
        f"(baseline rt {baseline_rt * 1000:.1f}ms)"
    )
