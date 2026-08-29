"""13.2 통합테스트 — 실제 dev DB 대상."""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingError, ListingService
from src.services.wallet_service import PLATFORM_HOUSE_USER_ID
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


async def _create_strategy(pool, owner_user_id) -> tuple[str, str]:
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


async def _never_eligible(strategy_id, version):
    return False


@pytest.fixture
def service(pool):
    return ListingService(pool, verify_paper_trading_eligibility=_always_eligible)


async def test_create_listing_starts_as_draft(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)

    listing = await service.create_listing(seller, strategy_id, version, Decimal("100.00"))

    assert listing.status == "DRAFT"
    assert listing.seller_user_id == seller
    assert listing.seller_type == "USER"


async def test_create_platform_listing_is_listed_immediately(service, pool):
    owner = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    listing = await service.create_platform_listing(strategy_id, version, Decimal("50.00"))

    assert listing.status == "LISTED"
    assert listing.seller_type == "PLATFORM"
    assert listing.seller_user_id == PLATFORM_HOUSE_USER_ID


async def test_create_platform_listing_rejects_nonexistent_strategy(service, pool):
    with pytest.raises(ListingError):
        await service.create_platform_listing("does-not-exist", "1.0.0", None)


async def test_create_listing_rejects_non_owner(service, pool):
    owner = await create_test_user(pool)
    other_user = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    with pytest.raises(ListingError):
        await service.create_listing(other_user, strategy_id, version, None)


async def test_create_listing_rejects_nonexistent_strategy(service, pool):
    seller = await create_test_user(pool)

    with pytest.raises(ListingError):
        await service.create_listing(seller, "does-not-exist", "1.0.0", None)


async def test_submit_for_verification_transitions_to_pending(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await service.create_listing(seller, strategy_id, version, None)

    submitted = await service.submit_for_verification(listing.id, seller)

    assert submitted.status == "PENDING_VERIFICATION"


async def test_submit_rejects_ineligible_strategy(pool):
    service = ListingService(pool, verify_paper_trading_eligibility=_never_eligible)
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await service.create_listing(seller, strategy_id, version, None)

    with pytest.raises(ListingError):
        await service.submit_for_verification(listing.id, seller)


async def test_submit_rejects_non_owner(service, pool):
    seller = await create_test_user(pool)
    other_user = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await service.create_listing(seller, strategy_id, version, None)

    with pytest.raises(ListingError):
        await service.submit_for_verification(listing.id, other_user)


async def test_submit_rejects_already_submitted_listing(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await service.create_listing(seller, strategy_id, version, None)
    await service.submit_for_verification(listing.id, seller)

    with pytest.raises(ListingError):
        await service.submit_for_verification(listing.id, seller)
