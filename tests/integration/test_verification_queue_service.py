"""18.1 통합테스트 — 실제 dev DB 대상."""
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.verification_queue_service import VerificationQueueService
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
    return VerificationQueueService(pool)


async def _always_eligible(strategy_id, version):
    return True


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


async def _submit_for_verification(pool, seller):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, None)
    return await listing_service.submit_for_verification(listing.id, seller)


async def test_pending_listing_appears_in_queue(service, pool):
    seller = await create_test_user(pool)
    verifier = await create_test_user(pool)
    listing = await _submit_for_verification(pool, seller)

    queue = await service.list_pending(verifier)

    assert any(item.listing_id == listing.id for item in queue)


async def test_own_listing_excluded_from_own_queue(service, pool):
    seller_and_verifier = await create_test_user(pool)
    listing = await _submit_for_verification(pool, seller_and_verifier)

    queue = await service.list_pending(seller_and_verifier)

    assert all(item.listing_id != listing.id for item in queue)


async def test_draft_listing_not_in_queue(service, pool):
    seller = await create_test_user(pool)
    verifier = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    draft = await listing_service.create_listing(seller, strategy_id, version, None)

    queue = await service.list_pending(verifier)

    assert all(item.listing_id != draft.id for item in queue)


async def test_no_pending_listings_returns_empty_not_error(service, pool):
    verifier = await create_test_user(pool)

    queue = await service.list_pending(verifier)

    assert isinstance(queue, list)
