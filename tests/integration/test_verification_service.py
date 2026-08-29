"""13.3 통합테스트 — 실제 dev DB 대상."""
import asyncio
import json
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.verification_service import VerificationError, VerificationService
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


async def _pending_listing(pool, seller):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, None)
    return await listing_service.submit_for_verification(listing.id, seller)


@pytest.fixture
def service(pool):
    return VerificationService(pool)


async def test_approve_transitions_to_listed(service, pool):
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier = await create_test_user(pool)

    result = await service.decide(listing.id, verifier, "APPROVE")

    assert result.status == "LISTED"
    assert result.rejection_reason is None

    async with pool.acquire() as conn:
        verified_at = await conn.fetchval(
            "SELECT verified_at FROM strategy_listings WHERE id = $1", listing.id
        )
    assert verified_at is not None


async def test_reject_returns_to_draft_with_reason(service, pool):
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier = await create_test_user(pool)

    result = await service.decide(
        listing.id, verifier, "REJECT", rejection_reason="오버피팅 의심"
    )

    assert result.status == "DRAFT"
    assert result.rejection_reason == "오버피팅 의심"


async def test_reject_without_reason_is_rejected(service, pool):
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier = await create_test_user(pool)

    with pytest.raises(VerificationError):
        await service.decide(listing.id, verifier, "REJECT")


async def test_cannot_decide_on_draft_listing(service, pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    strategy_id, version = await _create_strategy(pool, seller)
    draft_listing = await listing_service.create_listing(seller, strategy_id, version, None)
    verifier = await create_test_user(pool)

    with pytest.raises(VerificationError):
        await service.decide(draft_listing.id, verifier, "APPROVE")


async def test_concurrent_decisions_only_one_succeeds(service, pool):
    """docs/RED_TEAM_FINDINGS.md #05 회귀 — "읽고 나서 별도로 쓰기"였을 때는
    서로 다른 두 검증담당자가 같은 리스팅을 거의 동시에 하나는 승인, 하나는
    반려하면 나중에 커밋되는 쪽이 조용히 덮어썼다."""
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier_a = await create_test_user(pool)
    verifier_b = await create_test_user(pool)

    results = await asyncio.gather(
        service.decide(listing.id, verifier_a, "APPROVE"),
        service.decide(listing.id, verifier_b, "REJECT", rejection_reason="오버피팅 의심"),
        return_exceptions=True,
    )

    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, VerificationError)]
    assert len(successes) == 1
    assert len(failures) == 1

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_listings WHERE id = $1", listing.id
        )
    assert status == successes[0].status


async def test_unknown_decision_value_is_rejected(service, pool):
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier = await create_test_user(pool)

    with pytest.raises(VerificationError):
        await service.decide(listing.id, verifier, "MAYBE")
