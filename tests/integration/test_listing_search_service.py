"""13.8 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_search_service import ListingSearchService
from src.services.listing_service import ListingService
from src.services.verification_service import VerificationService
from tests.integration.conftest import create_test_user


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


@pytest.fixture
def service(pool):
    return ListingSearchService(pool)


async def _create_strategy(pool, owner_user_id, *, market="crypto", exchange="bitget"):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent)
            VALUES ($1, $2, $3, 'BTC/USDT', $4, $5, $6::jsonb, 'test-author')
            """,
            strategy_id,
            version,
            owner_user_id,
            market,
            exchange,
            json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id, version):
    return True


async def _listed_listing(pool, seller, *, price=None, market="crypto", exchange="bitget"):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(
        pool, seller, market=market, exchange=exchange
    )
    listing = await listing_service.create_listing(seller, strategy_id, version, price)
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    return await verification_service.decide(submitted.id, verifier, "APPROVE")


async def test_search_only_returns_listed_status(service, pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    await listing_service.create_listing(seller, strategy_id, version, None)  # DRAFT, 노출 안 됨
    listed = await _listed_listing(pool, seller)

    result = await service.search()

    ids = {item.id for item in result.items}
    assert listed.listing_id in ids


async def test_search_filters_by_asset_class(service, pool):
    seller = await create_test_user(pool)
    crypto_listing = await _listed_listing(pool, seller, market="crypto")
    equity_listing = await _listed_listing(pool, seller, market="kr_equity")

    result = await service.search(asset_class="kr_equity")

    ids = {item.id for item in result.items}
    assert equity_listing.listing_id in ids
    assert crypto_listing.listing_id not in ids


async def test_search_filters_by_max_price(service, pool):
    seller = await create_test_user(pool)
    cheap = await _listed_listing(pool, seller, price=Decimal("10.00"))
    expensive = await _listed_listing(pool, seller, price=Decimal("1000.00"))

    result = await service.search(max_price=Decimal("50.00"))

    ids = {item.id for item in result.items}
    assert cheap.listing_id in ids
    assert expensive.listing_id not in ids


async def test_no_matching_listings_returns_empty_not_error(service, pool):
    result = await service.search(exchange="does-not-exist")

    assert result.items == []
    assert result.total == 0


async def test_sort_is_by_verified_at_descending_not_created_at(service, pool):
    """완료조건 실증 — 나중에 검증통과된 것이 먼저 등록됐어도 앞서 나온다
    (동일 생성일이라도 재등록 조작 방지, 14번 §14.4.3)."""
    seller = await create_test_user(pool)
    older_listing = await _listed_listing(pool, seller)  # 먼저 등록+검증
    newer_listing = await _listed_listing(pool, seller)  # 나중에 등록+검증

    result = await service.search(page_size=100)

    ids_in_order = [item.id for item in result.items]
    assert ids_in_order.index(newer_listing.listing_id) < ids_in_order.index(
        older_listing.listing_id
    )


async def test_sort_by_sharpe_ratio_orders_purely_by_performance(service, pool):
    """ZuluRank식 랭킹(2026-09-02 신설) — sort_by="SHARPE_RATIO"는 검증통과일과
    무관하게 샤프비율 내림차순으로만 정렬한다."""
    seller = await create_test_user(pool)
    low_sharpe = await _listed_listing(pool, seller)
    high_sharpe = await _listed_listing(pool, seller)  # 나중에 검증통과(기본 정렬이면 더 앞)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_listings SET sharpe_ratio = 0.5 WHERE id = $1", low_sharpe.listing_id
        )
        await conn.execute(
            "UPDATE strategy_listings SET sharpe_ratio = 2.5 WHERE id = $1",
            high_sharpe.listing_id,
        )

    result = await service.search(sort_by="SHARPE_RATIO", page_size=100)

    ids_in_order = [item.id for item in result.items]
    assert ids_in_order.index(high_sharpe.listing_id) < ids_in_order.index(low_sharpe.listing_id)


async def test_pagination_limits_and_offsets(service, pool):
    seller = await create_test_user(pool)
    for _ in range(3):
        await _listed_listing(pool, seller)

    page1 = await service.search(page=1, page_size=2)
    page2 = await service.search(page=2, page_size=2)

    assert len(page1.items) == 2
    assert page1.total >= 3
    overlap = {item.id for item in page1.items} & {item.id for item in page2.items}
    assert overlap == set()
