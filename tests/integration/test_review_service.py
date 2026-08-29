"""13.9 통합테스트 — 실제 dev DB 대상.

30일 대기 실제 경과 대신, DB의 purchased_at을 직접 과거로 돌려
"30일이 지난 상태"를 결정적으로 재현한다.
"""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.review_service import ReviewError, ReviewService
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
    return ReviewService(pool)


async def _create_strategy(pool, owner_user_id):
    strategy_id = f"test-strategy-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO strategies
                (strategy_id, version, owner_user_id, target_asset, market, exchange,
                 fsm_definition, author_agent)
            VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', $4::jsonb, 'test-author')
            """,
            strategy_id,
            version,
            owner_user_id,
            json.dumps({}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id, version):
    return True


async def _create_listed_listing(pool, seller):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    return approved.listing_id


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            user_id,
            amount,
        )


async def _purchase_listing(pool, listing_id, buyer, *, days_ago=31):
    purchase_service = PurchaseService(pool)
    await _fund_wallet(pool, buyer, Decimal("10"))
    result = await purchase_service.purchase(buyer, listing_id)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_purchases SET purchased_at = now() - make_interval(days => $2) "
            "WHERE id = $1",
            result.purchase_id,
            days_ago,
        )
    return result


async def _purchase(pool, seller, buyer, *, days_ago=31):
    listing_id = await _create_listed_listing(pool, seller)
    await _purchase_listing(pool, listing_id, buyer, days_ago=days_ago)
    return listing_id


async def test_create_review_succeeds_after_30_days(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=31)

    review = await service.create_review(buyer, listing_id, 5, comment="좋아요")

    assert review.rating == 5
    assert review.listing_id == listing_id


async def test_create_review_rejected_before_30_days(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=10)

    with pytest.raises(ReviewError):
        await service.create_review(buyer, listing_id, 5)


async def test_create_review_rejected_without_purchase_history(service, pool):
    seller = await create_test_user(pool)
    stranger = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, None)

    with pytest.raises(ReviewError):
        await service.create_review(stranger, listing.id, 5)


async def test_duplicate_review_rejected(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=31)
    await service.create_review(buyer, listing_id, 4)

    with pytest.raises(ReviewError):
        await service.create_review(buyer, listing_id, 2)


async def test_invalid_rating_rejected(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=31)

    with pytest.raises(ReviewError):
        await service.create_review(buyer, listing_id, 6)


async def test_list_reviews_returns_all_regardless_of_count(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=31)
    await service.create_review(buyer, listing_id, 3, comment="괜찮음")

    reviews = await service.list_reviews(listing_id)

    assert len(reviews) == 1
    assert reviews[0].comment == "괜찮음"


async def test_rating_summary_hidden_below_threshold(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_id = await _purchase(pool, seller, buyer, days_ago=31)
    await service.create_review(buyer, listing_id, 5)

    summary = await service.get_rating_summary(listing_id)

    assert summary.review_count == 1
    assert summary.average_rating is None


async def test_rating_summary_shown_at_threshold(service, pool):
    seller = await create_test_user(pool)
    listing_id = await _create_listed_listing(pool, seller)
    for _ in range(5):
        buyer = await create_test_user(pool)
        await _purchase_listing(pool, listing_id, buyer, days_ago=31)
        await service.create_review(buyer, listing_id, 4)

    summary = await service.get_rating_summary(listing_id)

    assert summary.review_count == 5
    assert summary.average_rating == 4.0
