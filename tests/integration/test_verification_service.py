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


async def test_reject_reason_is_persisted_and_published(pool):
    """docs/RED_TEAM_FINDINGS.md #16 회귀 — 반려 사유가 DB에도 이벤트
    페이로드에도 남아야 한다(응답이 나간 순간 사라지면 안 됨)."""
    published = []

    async def publish(topic, payload):
        published.append((topic, payload))

    service = VerificationService(pool, publish=publish)
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier = await create_test_user(pool)

    await service.decide(
        listing.id, verifier, "REJECT", rejection_reason="Look-ahead Bias 의심"
    )

    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT rejection_reason FROM strategy_listings WHERE id = $1", listing.id
        )
    assert stored == "Look-ahead Bias 의심"

    topic, payload = published[0]
    assert topic == "strategy.verification.completed"
    assert payload["rejection_reason"] == "Look-ahead Bias 의심"


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


async def test_concurrent_decisions_only_one_succeeds(service, pool, monkeypatch):
    """docs/RED_TEAM_FINDINGS.md #05 회귀 — "읽고 나서 별도로 쓰기"였을 때는
    서로 다른 두 검증담당자가 같은 리스팅을 거의 동시에 하나는 승인, 하나는
    반려하면 나중에 커밋되는 쪽이 조용히 덮어썼다.

    asyncio.gather만으로는 두 decide() 호출의 사전조회(pre_check)가 실제로
    동시에 겹친다는 보장이 없다 — 커넥션 풀 라운드트립이 우연히 어긋나면
    첫 호출이 UPDATE까지 완전히 끝난 뒤 두 번째가 시작돼(정상적인 순차
    처리) 원래 보고된 레이스가 재현되지 않을 수 있다. #04(test_approval_
    service.py)와 같은 원칙 — barrier로 두 호출의 사전조회가 반드시 같은
    시점에 끝나도록 강제해 "둘 다 PENDING_VERIFICATION을 봤다"는 레이스
    조건을 결정적으로 재현한다."""
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)
    verifier_a = await create_test_user(pool)
    verifier_b = await create_test_user(pool)

    arrived = 0
    released = asyncio.Event()
    original_fetchrow = asyncpg.pool.PoolConnectionProxy.fetchrow

    async def _synced_fetchrow(self, query, *args, **kwargs):
        nonlocal arrived
        result = await original_fetchrow(self, query, *args, **kwargs)
        if "SELECT status, seller_user_id FROM strategy_listings" in query:
            arrived += 1
            if arrived >= 2:
                released.set()
            else:
                await released.wait()
        return result

    monkeypatch.setattr(asyncpg.pool.PoolConnectionProxy, "fetchrow", _synced_fetchrow)

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


async def test_verifier_cannot_decide_own_listing(service, pool):
    """전수감사 §2 / 15번 §15.6 — 검증담당자는 자기 리스팅을 승인·반려할 수 없다."""
    seller = await create_test_user(pool)
    listing = await _pending_listing(pool, seller)

    with pytest.raises(VerificationError, match="이해상충"):
        await service.decide(listing.id, seller, "APPROVE")

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM strategy_listings WHERE id = $1", listing.id
        )
    assert status == "PENDING_VERIFICATION"
