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


async def _always_eligible(strategy_id, version, seller_user_id=None):
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


async def test_listed_listing_excluded_from_queue(service, pool):
    """negative — 이미 승인되어 status='LISTED'가 된 리스팅은 대기열
    불변식(PENDING_VERIFICATION만 노출) 위반이므로 다시 노출되면 안 된다."""
    seller = await create_test_user(pool)
    verifier = await create_test_user(pool)
    listing = await _submit_for_verification(pool, seller)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_listings SET status = 'LISTED' WHERE id = $1", listing.id
        )

    queue = await service.list_pending(verifier)

    assert all(item.listing_id != listing.id for item in queue)


async def test_delisted_listing_excluded_from_queue(service, pool):
    """negative — status='DELISTED'인 리스팅은 대기열에 노출되면 안 된다."""
    seller = await create_test_user(pool)
    verifier = await create_test_user(pool)
    listing = await _submit_for_verification(pool, seller)
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_listings SET status = 'DELISTED' WHERE id = $1", listing.id
        )

    queue = await service.list_pending(verifier)

    assert all(item.listing_id != listing.id for item in queue)


async def test_malformed_verifier_id_raises_instead_of_silently_matching(service, pool):
    """negative — verifier_user_id는 UUID 컬럼과 비교되는 불변식을 갖는다.
    형식이 깨진 값(uuid로 캐스팅 불가한 문자열)을 넣으면 조용히 빈 목록을
    반환하는 대신 예외를 던져야 한다(잘못된 verifier로 오탐 없이 빈 큐를
    반환하면 "queue가 비어 있다"는 신호와 "verifier id가 틀렸다"는 신호가
    구분되지 않아 위험하다)."""
    with pytest.raises(asyncpg.exceptions.DataError):
        await service.list_pending("not-a-valid-uuid")  # type: ignore[arg-type]


async def test_list_pending_propagates_db_failure_instead_of_returning_empty(service, monkeypatch):
    """실패주입 — pool.acquire()가 예외를 던지면(연결 장애 등) list_pending은
    그 예외를 그대로 전파해야 한다. 여기서 예외를 삼키고 빈 리스트를 반환하면
    "검증 대기열이 비었다"와 "DB 장애로 조회 실패"가 구분되지 않아
    fail-closed 기본 태세(CLAUDE.md §3) 위반이다."""

    class _BoomAcquire:
        def acquire(self):
            raise ConnectionError("simulated pool exhaustion")

    monkeypatch.setattr(service, "_pool", _BoomAcquire())

    with pytest.raises(ConnectionError):
        await service.list_pending(uuid4())
