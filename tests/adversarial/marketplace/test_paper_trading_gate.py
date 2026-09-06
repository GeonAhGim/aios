"""task-1721 P1-B 적대적 — "3개월 Paper Trading 이력" 게이트가 실제로
거부하는지 확인한다.

Spec: 감사 2026-09-06 P1-B. 이전에는 `src/api/marketplace_deps.py`의
`_always_eligible`이 strategy_executions을 전혀 조회하지 않고 무조건
True를 반환해, "3개월 이력" 요건이 게이트가 아니라 장식이었다(FULL_AUDIT
2026-09-02.md §4 "리스팅 게이트 열림"). 지금은
`src.services.paper_trading_eligibility.check_paper_trading_eligibility`가
strategy_executions을 실제로 조회한다 — 이 파일은 그 조회가 없거나
기간 미달인 계정을 실제로 막는지, ListingService.submit_for_verification
경로 전체(DI 콜백 포함)로 검증한다.

task-1803(리뷰 task-1799 REJECT 후속) — 잔여 결함 2건에 대한 적대적 단언:
(a) DB 조회가 asyncpg.PostgresError를 던지면 check_paper_trading_eligibility가
    예외를 전파하지 않고 False(거부)를 반환한다(fail-closed).
(b) 리스팅을 시도하는 판매자 A의 (strategy_id, version) 3개월 PAPER 이력이
    전부 타 사용자 B 소유일 때는 거부되고, 같은 이력이 A 소유일 때만
    통과한다(strategy_executions.user_id 스코프).
"""
from __future__ import annotations

import json
from decimal import Decimal
from functools import partial
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingError, ListingService
from src.services.paper_trading_eligibility import check_paper_trading_eligibility
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _create_strategy(pool, owner_user_id) -> tuple[str, str]:
    strategy_id = f"test-ptg-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent) "
            "VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')",
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _insert_execution(
    pool, strategy_id, version, user_id, *, mode="PAPER", days_ago: int
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategy_executions "
            "(strategy_id, strategy_version, user_id, exchange, mode, allocated_capital, "
            " started_at) "
            "VALUES ($1, $2, $3, 'bitget', $4, 1000, now() - ($5 * interval '1 day'))",
            strategy_id,
            version,
            user_id,
            mode,
            days_ago,
        )


def _service(pool) -> ListingService:
    return ListingService(
        pool, verify_paper_trading_eligibility=partial(check_paper_trading_eligibility, pool)
    )


async def test_check_paper_trading_eligibility_denies_with_no_execution_history(pool) -> None:
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller)

    assert eligible is False


async def test_check_paper_trading_eligibility_denies_short_history(pool) -> None:
    """1개월밖에 안 된 PAPER 실행은 3개월 요건 미달로 거부돼야 한다."""
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _insert_execution(pool, strategy_id, version, seller, days_ago=30)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller)

    assert eligible is False


async def test_check_paper_trading_eligibility_denies_live_only_history(pool) -> None:
    """LIVE 실행은 요건이 PAPER를 명시하므로 3개월이 지났어도 인정되지 않는다."""
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _insert_execution(pool, strategy_id, version, seller, mode="LIVE", days_ago=120)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller)

    assert eligible is False


async def test_check_paper_trading_eligibility_allows_qualifying_history(pool) -> None:
    """양성 대조군 — 3개월을 넘긴 PAPER 실행이 있으면 실제로 통과해야 한다
    (게이트가 항상 거부로만 뒤집힌 게 아님을 확인)."""
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _insert_execution(pool, strategy_id, version, seller, days_ago=120)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller)

    assert eligible is True


async def test_submit_for_verification_rejects_account_without_paper_history(pool) -> None:
    """DoD — 이력 부족 계정의 리스팅 시도는 submit_for_verification 단계에서
    거부되고, 리스팅은 DRAFT에 머물러야 한다."""
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    service = _service(pool)
    listing = await service.create_listing(seller, strategy_id, version, Decimal("10.00"))

    with pytest.raises(ListingError, match="3개월 이상의 Paper Trading 이력"):
        await service.submit_for_verification(listing.id, seller)

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_listings WHERE id = $1", listing.id
        )
    assert status == "DRAFT"


async def test_submit_for_verification_accepts_account_with_qualifying_history(pool) -> None:
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _insert_execution(pool, strategy_id, version, seller, days_ago=95)
    service = _service(pool)
    listing = await service.create_listing(seller, strategy_id, version, Decimal("10.00"))

    submitted = await service.submit_for_verification(listing.id, seller)

    assert submitted.status == "PENDING_VERIFICATION"


class _ExplodingConnection:
    async def fetchval(self, *args, **kwargs):
        raise asyncpg.PostgresConnectionError("simulated DB outage")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _ExplodingPool:
    """`pool.acquire()`가 반환하는 커넥션의 `fetchval`이 항상
    `asyncpg.PostgresError`를 던지는 가짜 pool — 실 DB 장애를 흉내낸다."""

    def acquire(self):
        return _ExplodingConnection()


async def test_check_paper_trading_eligibility_denies_on_db_error() -> None:
    """DoD(a) — fetchval이 asyncpg.PostgresError를 던지는 pool을 주입하면
    check_paper_trading_eligibility가 예외를 전파하지 않고 False(거부)를
    반환해야 한다(fail-closed)."""
    eligible = await check_paper_trading_eligibility(
        _ExplodingPool(), "strategy-x", "1.0.0", uuid4()
    )

    assert eligible is False


async def test_check_paper_trading_eligibility_denies_other_users_paper_history(pool) -> None:
    """DoD(b) 전반부 — 판매자 A가 리스팅하려는 (strategy_id, version)의
    3개월 PAPER 실행이 전부 타 사용자 B 소유일 때는 A 기준으로 거부돼야
    한다(strategy_executions.user_id 스코프 누락 시 통과하던 결함)."""
    seller_a = await create_test_user(pool)
    other_user_b = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller_a)
    await _insert_execution(pool, strategy_id, version, other_user_b, days_ago=120)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller_a)

    assert eligible is False


async def test_check_paper_trading_eligibility_allows_when_history_owned_by_seller(pool) -> None:
    """DoD(b) 후반부 — 같은 이력이 판매자 A 본인 소유일 때만 통과해야 한다
    (양성 대조군 — 스코프 추가가 정당한 이력까지 막지 않음을 확인)."""
    seller_a = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller_a)
    await _insert_execution(pool, strategy_id, version, seller_a, days_ago=120)

    eligible = await check_paper_trading_eligibility(pool, strategy_id, version, seller_a)

    assert eligible is True


async def test_submit_for_verification_rejects_seller_relying_on_other_users_history(
    pool,
) -> None:
    """DoD(b) — ListingService.submit_for_verification 경로 전체(DI 콜백
    포함)로도 타 사용자 이력에 기댄 리스팅이 거부되는지 확인한다."""
    seller_a = await create_test_user(pool)
    other_user_b = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller_a)
    await _insert_execution(pool, strategy_id, version, other_user_b, days_ago=120)
    service = _service(pool)
    listing = await service.create_listing(seller_a, strategy_id, version, Decimal("10.00"))

    with pytest.raises(ListingError, match="3개월 이상의 Paper Trading 이력"):
        await service.submit_for_verification(listing.id, seller_a)
