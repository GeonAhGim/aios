"""13.5 통합테스트 — 실제 dev DB 대상.

ADR-2026-08-29 §1 반영 — 구매는 지갑 차감으로 즉시 CONFIRMED되므로,
구 PENDING_PAYMENT 중간 상태를 전제하던 시나리오(수동 `_confirm_payment`)는
더 이상 재현 불가능해 제거했다. 구매 성공 = 실행 접근권한 즉시 부여를
직접 검증한다.
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
from src.services.strategy_access_service import StrategyAccessError, StrategyAccessService
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
    return StrategyAccessService(pool)


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
            json.dumps({"states": ["IDLE"]}),
        )
    return strategy_id, version


async def _always_eligible(strategy_id, version):
    return True


async def _listed_strategy(pool, seller):
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    return strategy_id, version, approved.listing_id


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            user_id,
            amount,
        )


async def _funded_purchase(pool, listing_id, buyer):
    await _fund_wallet(pool, buyer, Decimal("10"))
    purchase_service = PurchaseService(pool)
    return await purchase_service.purchase(buyer, listing_id)


async def test_owner_can_always_access(service, pool):
    owner = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    assert await service.can_access(owner, strategy_id, version) is True


async def test_stranger_cannot_access(service, pool):
    owner = await create_test_user(pool)
    stranger = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    assert await service.can_access(stranger, strategy_id, version) is False


async def test_buyer_gains_access_immediately_after_purchase(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version, listing_id = await _listed_strategy(pool, seller)
    buyer = await create_test_user(pool)

    await _funded_purchase(pool, listing_id, buyer)

    assert await service.can_access(buyer, strategy_id, version) is True


async def test_get_strategy_for_execution_raises_for_unauthorized_user(service, pool):
    owner = await create_test_user(pool)
    stranger = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    with pytest.raises(StrategyAccessError):
        await service.get_strategy_for_execution(stranger, strategy_id, version)


async def test_get_strategy_for_execution_returns_definition_for_buyer(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version, listing_id = await _listed_strategy(pool, seller)
    buyer = await create_test_user(pool)
    await _funded_purchase(pool, listing_id, buyer)

    definition = await service.get_strategy_for_execution(buyer, strategy_id, version)

    assert definition.owner_user_id == seller
    assert definition.fsm_definition == {"states": ["IDLE"]}


async def test_access_survives_seller_delisting_after_purchase(service, pool):
    seller = await create_test_user(pool)
    strategy_id, version, listing_id = await _listed_strategy(pool, seller)
    buyer = await create_test_user(pool)
    await _funded_purchase(pool, listing_id, buyer)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategy_listings SET status = 'DELISTED' WHERE id = $1", listing_id
        )

    assert await service.can_access(buyer, strategy_id, version) is True


async def test_seller_never_receives_buyer_identifying_data(service, pool):
    """13.6 완료조건(정책문서 10.3-B 실증) — 판매자가 자기 전략을 조회해도
    StrategyDefinition에는 buyer_user_id/구매내역 등 구매자 식별 정보가
    구조적으로 존재하지 않는다(FD-16 실행 인스턴스 자체가 없어 노출할
    데이터가 아예 없다는 사실을 스키마 레벨로 고정 — 나중에 누군가
    실수로 buyer 필드를 추가하면 이 테스트가 잡아낸다)."""
    seller = await create_test_user(pool)
    strategy_id, version, listing_id = await _listed_strategy(pool, seller)
    buyer = await create_test_user(pool)
    await _funded_purchase(pool, listing_id, buyer)

    definition = await service.get_strategy_for_execution(seller, strategy_id, version)

    assert set(type(definition).model_fields) == {
        "strategy_id",
        "version",
        "owner_user_id",
        "fsm_definition",
    }
    assert definition.owner_user_id == seller
