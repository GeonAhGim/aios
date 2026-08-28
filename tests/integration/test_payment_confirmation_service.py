"""18.5a/18.5b/18.6 통합테스트 — 실제 dev DB 대상.

18.6 완료조건 3가지를 여기서 함께 실증한다: PENDING_PAYMENT 목록만
노출(CONFIRMED 제외), PENDING_PAYMENT 상태에서 StrategyAccessService
(13.5) 실행권한 미부여, 결제확인 중복요청 시 audit_log 1건만 기록.
"""
import json
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.listing_service import ListingService
from src.services.payment_confirmation_service import (
    PaymentConfirmationError,
    PaymentConfirmationService,
)
from src.services.purchase_service import PurchaseService
from src.services.strategy_access_service import StrategyAccessService
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
    return PaymentConfirmationService(pool)


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


async def _create_pending_purchase(pool):
    seller = await create_test_user(pool)
    buyer = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    purchase_service = PurchaseService(pool)

    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")
    purchase = await purchase_service.purchase(buyer, approved.listing_id)
    return purchase.purchase_id, buyer, strategy_id, version


async def test_pending_list_excludes_confirmed(service, pool):
    purchase_id, buyer, strategy_id, version = await _create_pending_purchase(pool)

    page_before = await service.list_pending_payments(page_size=1000)
    assert any(item.purchase_id == purchase_id for item in page_before.items)

    admin = await create_test_user(pool)
    await service.confirm_payment(purchase_id, admin, idempotency_key="key-1")

    page_after = await service.list_pending_payments(page_size=1000)
    assert all(item.purchase_id != purchase_id for item in page_after.items)


async def test_pending_payment_blocks_execution_access(pool):
    purchase_id, buyer, strategy_id, version = await _create_pending_purchase(pool)
    access_service = StrategyAccessService(pool)

    assert await access_service.can_access(buyer, strategy_id, version) is False


async def test_confirm_payment_opens_execution_access(service, pool):
    purchase_id, buyer, strategy_id, version = await _create_pending_purchase(pool)
    access_service = StrategyAccessService(pool)
    admin = await create_test_user(pool)

    await service.confirm_payment(purchase_id, admin, idempotency_key="key-1")

    assert await access_service.can_access(buyer, strategy_id, version) is True


async def test_duplicate_confirm_requests_write_audit_log_once(service, pool):
    purchase_id, buyer, strategy_id, version = await _create_pending_purchase(pool)
    admin = await create_test_user(pool)

    await service.confirm_payment(purchase_id, admin, idempotency_key="key-1")
    await service.confirm_payment(purchase_id, admin, idempotency_key="key-2")
    result = await service.confirm_payment(purchase_id, admin, idempotency_key="key-3")

    assert result.payment_status == "CONFIRMED"
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE action_type = 'payment.confirmed' AND target_id = $1",
            str(purchase_id),
        )
    assert count == 1


async def test_confirm_payment_rejects_nonexistent_purchase(service, pool):
    admin = await create_test_user(pool)
    with pytest.raises(PaymentConfirmationError):
        await service.confirm_payment(999999999, admin, idempotency_key="key-1")


async def test_confirm_payment_returns_stable_confirmed_at(service, pool):
    purchase_id, buyer, strategy_id, version = await _create_pending_purchase(pool)
    admin = await create_test_user(pool)

    first = await service.confirm_payment(purchase_id, admin, idempotency_key="key-1")
    second = await service.confirm_payment(purchase_id, admin, idempotency_key="key-2")

    assert first.confirmed_at == second.confirmed_at
