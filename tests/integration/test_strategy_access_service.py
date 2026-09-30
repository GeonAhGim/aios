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


async def _always_eligible(strategy_id, version, seller_user_id=None):
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


async def _unconfirmed_purchase(pool, strategy_id, version, seller, buyer) -> None:
    """payment_status 기본값('PENDING_PAYMENT')인 구매 레코드를 직접 삽입한다.

    ADR-2026-08-29 §1 이후 PurchaseService는 항상 CONFIRMED로 즉시 확정하므로
    이 상태는 서비스 경로로는 재현 불가능하지만, DB 제약(CHECK) 자체는 여전히
    PENDING_PAYMENT를 허용한다 — 마이그레이션 이전 잔존 데이터나 수동 조작으로
    이런 행이 존재해도 접근권한이 새지 않아야 한다는 불변식을 검증한다.
    """
    async with pool.acquire() as conn:
        listing_id = await conn.fetchval(
            "INSERT INTO strategy_listings "
            "(strategy_id, strategy_version, seller_user_id, price, status) "
            "VALUES ($1, $2, $3, 10, 'LISTED') RETURNING id",
            strategy_id,
            version,
            seller,
        )
        await conn.execute(
            "INSERT INTO strategy_purchases (listing_id, buyer_user_id, price_paid) "
            "VALUES ($1, $2, 10)",
            listing_id,
            buyer,
        )


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


async def test_can_access_returns_false_for_nonexistent_strategy(service, pool):
    stranger = await create_test_user(pool)

    assert await service.can_access(stranger, "does-not-exist", "9.9.9") is False


async def test_get_strategy_for_execution_raises_for_nonexistent_strategy(service, pool):
    stranger = await create_test_user(pool)

    with pytest.raises(StrategyAccessError):
        await service.get_strategy_for_execution(stranger, "does-not-exist", "9.9.9")


async def test_buyer_with_unconfirmed_payment_cannot_access(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _unconfirmed_purchase(pool, strategy_id, version, seller, buyer)

    assert await service.can_access(buyer, strategy_id, version) is False


async def test_get_strategy_for_execution_raises_for_unconfirmed_payment(service, pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _unconfirmed_purchase(pool, strategy_id, version, seller, buyer)

    with pytest.raises(StrategyAccessError):
        await service.get_strategy_for_execution(buyer, strategy_id, version)


async def test_can_access_propagates_db_failure_instead_of_granting_access(
    service, pool, monkeypatch
):
    """실패주입 — pool.acquire()가 예외를 던지면(연결 장애 등) can_access는
    그 예외를 그대로 전파해야 한다. 여기서 예외를 삼키고 True/False 중 아무
    값이나 반환하면 fail-closed 기본 태세(CLAUDE.md §3) 위반이다."""
    owner = await create_test_user(pool)
    strategy_id, version = await _create_strategy(pool, owner)

    class _BoomAcquire:
        def acquire(self):
            raise ConnectionError("simulated pool exhaustion")

    monkeypatch.setattr(service, "_pool", _BoomAcquire())

    with pytest.raises(ConnectionError):
        await service.can_access(owner, strategy_id, version)
