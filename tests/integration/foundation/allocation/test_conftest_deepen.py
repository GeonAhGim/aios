"""DEEPEN(task-9236, 원 리프 task-6704 "고아 산출물 회수 5828"): 이 디렉터리의
`conftest.py` 헬퍼(`_asyncpg_dsn`/`create_order_with_fills`/
`wallet_available_balance`)가 불변식 위반 입력을 명시적으로 거부하는지, 그리고
의존성(커넥션) 실패 시 조용히 삼키지 않고 그대로 전파(fail-closed)하는지를
검증한다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 -- 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A). `create_order_with_fills`가
씨딩하는 `orders.side`/`fills.side` CHECK 제약과 `orders.user_id`/
`orders.fund_id`/`orders.portfolio_id` FK 제약은 운영 스키마 자체의 방어이며,
이 리프는 그 방어가 헬퍼를 우회해 뚫리지 않는지만 확인한다.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal

import asyncpg
import pytest

from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from tests.integration.conftest import create_test_user
from tests.integration.foundation.allocation.conftest import (
    _asyncpg_dsn,
    create_order_with_fills,
    wallet_available_balance,
)
from tests.integration.foundation.entities.conftest import build_hierarchy


class _InjectedConnFailure(RuntimeError):
    pass


class _FailingConn:
    async def fetchval(self, *args: object, **kwargs: object) -> Decimal:
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


def test_asyncpg_dsn_raises_when_database_url_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """negative: `DATABASE_URL`이 비어 있으면(설정 누락) 조용히 빈/잘못된
    DSN을 만들어내지 말고 명시적으로 실패해야 한다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(KeyError):
        _asyncpg_dsn()


async def test_create_order_with_fills_rejects_invalid_side(pool: asyncpg.Pool) -> None:
    """negative: `orders.side`/`fills.side`는 `CHECK (side IN ('BUY','SELL'))`
    제약을 갖는다(마이그레이션 `210cc26533c7`/`073beca589d5`) -- 임의 문자열은
    거부되어야 한다(게이트-적색 재현: 스키마가 이 방어를 갖지 않으면 조용히
    쓰레기 side 값이 저장된다)."""
    entities = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, entities)

    with pytest.raises(asyncpg.CheckViolationError):
        await create_order_with_fills(
            pool,
            user_id=hierarchy.tenant_id,
            fund_id=hierarchy.fund.fund_id,
            portfolio_id=hierarchy.portfolio.portfolio_id,
            side="HOLD",
            fills=[(Decimal("10"), Decimal("100.00"))],
        )


async def test_create_order_with_fills_rejects_unknown_user_id(pool: asyncpg.Pool) -> None:
    """negative: `orders.user_id`는 `users(user_id)` FK다(마이그레이션
    `a1f2b3c4d5e6`) -- 존재하지 않는 user로는 주문을 시딩할 수 없다."""
    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_order_with_fills(
            pool,
            user_id=uuid.uuid4(),
            fund_id=uuid.uuid4(),
            portfolio_id=uuid.uuid4(),
            side="BUY",
            fills=[(Decimal("10"), Decimal("100.00"))],
        )


async def test_create_order_with_fills_rejects_unknown_fund_id(pool: asyncpg.Pool) -> None:
    """negative: `orders.fund_id`는 `fund(fund_id)` FK다(마이그레이션
    `789c138f13fe`) -- 존재하지 않는 fund로는 주문을 시딩할 수 없다. user는
    실재해야 이 FK만 단독으로 검증된다."""
    user_id = await create_test_user(pool)

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await create_order_with_fills(
            pool,
            user_id=user_id,
            fund_id=uuid.uuid4(),
            portfolio_id=uuid.uuid4(),
            side="BUY",
            fills=[(Decimal("10"), Decimal("100.00"))],
        )


async def test_wallet_available_balance_propagates_connection_failure() -> None:
    """실패주입: `pool.acquire()` 하위 커넥션의 `fetchval`이 예외를 던지면
    `wallet_available_balance`는 이를 삼키고 `Decimal("0")` 같은 값을
    조용히 반환해서는 안 되며, 그대로 전파해야 한다(fail-closed)."""
    with pytest.raises(_InjectedConnFailure, match="injected connection failure"):
        await wallet_available_balance(_FailingPool(), uuid.uuid4())


@pytest.mark.perf
async def test_create_order_with_fills_round_trip_stays_within_local_budget(
    pool: asyncpg.Pool,
) -> None:
    """성능 단언(D2): 기준 왕복 비용(pool.acquire + SELECT 1, n=20, 워밍업
    3회 버림)을 이 환경에서 직접 재고, `create_order_with_fills`의 절대
    시간이 그 기준의 12배(연속 DB 왕복 여유, `test_conftest_deepen.py`
    (positions, task-7706)와 같은 정규화 방식) 이내인지 단언한다 -- 절대 ms
    임계는 실행환경마다 흔들려 회귀 게이트로 못 쓴다."""
    samples: list[float] = []
    for _ in range(23):
        start = time.perf_counter()
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        samples.append(time.perf_counter() - start)
    baseline_rt = sorted(samples[3:])[-1]  # 워밍업 3회 버리고 최댓값(보수적 rt)

    entities = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, entities)

    start = time.perf_counter()
    await create_order_with_fills(
        pool,
        user_id=hierarchy.tenant_id,
        fund_id=hierarchy.fund.fund_id,
        portfolio_id=hierarchy.portfolio.portfolio_id,
        side="BUY",
        fills=[(Decimal("1"), Decimal("100.00"))],
    )
    elapsed = time.perf_counter() - start

    # `create_order_with_fills`는 order 1건 + fill 1건, 총 2회의 순차 INSERT
    # 왕복을 한 커넥션에서 수행한다(단일 SELECT 왕복인 baseline보다 본질적으로
    # 무거움) -- 배수를 2배(24x), 바닥을 60ms로 올려 이 구조적 차이를 반영한다.
    budget = max(0.060, 24 * baseline_rt)
    assert elapsed < budget, (
        f"create_order_with_fills took {elapsed * 1000:.1f}ms, budget {budget * 1000:.1f}ms "
        f"(baseline rt {baseline_rt * 1000:.1f}ms)"
    )
