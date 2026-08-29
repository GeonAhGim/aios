"""15.3 통합테스트 — 3개 훅 지점(구매/배포승인/ApprovalMode 변경) 실제 dev DB 대상."""
import json
from decimal import Decimal
from functools import partial
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.services.approval_settings_service import ApprovalSettingsError, ApprovalSettingsService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseError, PurchaseService
from src.services.risk_matching import RiskMatchingError, check_purchase_risk_warning
from src.services.strategy_builder_service import StrategyBuilderService, StrategyLifecycleError
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


async def _set_risk_profile(pool, user_id, profile):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE users SET risk_profile = $2, risk_profile_assessed_at = now() "
            "WHERE user_id = $1",
            user_id,
            profile,
        )


async def _set_strategy_risk_level(pool, strategy_id, version, risk_level):
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE strategies SET risk_level = $3 WHERE strategy_id = $1 AND version = $2",
            strategy_id,
            version,
            risk_level,
        )


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


async def _fund_wallet(pool, user_id, amount) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO user_wallets (user_id, balance) VALUES ($1, $2) "
            "ON CONFLICT (user_id) DO UPDATE SET balance = user_wallets.balance + $2",
            user_id,
            amount,
        )


# ---------- 훅① 구매(PurchaseService.check_risk_warning) ----------


async def test_purchase_risk_check_warns_for_conservative_buyer(pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    await _set_strategy_risk_level(pool, strategy_id, version, "공격형")
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")

    buyer = await create_test_user(pool)
    await _set_risk_profile(pool, buyer, "안정형")
    await _fund_wallet(pool, buyer, Decimal("10"))
    purchase_service = PurchaseService(
        pool, check_risk_warning=partial(check_purchase_risk_warning, pool)
    )

    with pytest.raises(PurchaseError):
        await purchase_service.purchase(buyer, approved.listing_id)

    result = await purchase_service.purchase(
        buyer, approved.listing_id, risk_warning_acknowledged=True
    )
    assert result.risk_warning is not None


async def test_purchase_risk_check_raises_for_unassessed_buyer(pool):
    seller = await create_test_user(pool)
    listing_service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)
    verification_service = VerificationService(pool)
    strategy_id, version = await _create_strategy(pool, seller)
    listing = await listing_service.create_listing(seller, strategy_id, version, Decimal("10"))
    submitted = await listing_service.submit_for_verification(listing.id, seller)
    verifier = await create_test_user(pool)
    approved = await verification_service.decide(submitted.id, verifier, "APPROVE")

    buyer = await create_test_user(pool)  # risk_profile 미지정
    purchase_service = PurchaseService(
        pool, check_risk_warning=partial(check_purchase_risk_warning, pool)
    )

    with pytest.raises(RiskMatchingError):
        await purchase_service.purchase(buyer, approved.listing_id)


# ---------- 훅② 배포승인(StrategyBuilderService.transition_lifecycle) ----------


async def test_deploy_approval_warns_when_owner_more_conservative_than_strategy(pool):
    owner = await create_test_user(pool)
    await _set_risk_profile(pool, owner, "안정형")
    builder = StrategyBuilderService(pool)
    strategy_id = f"test-{uuid4().hex[:8]}"
    await builder.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await _set_strategy_risk_level(pool, strategy_id, "1.0.0", "공격형")

    for stage in ("BACKTESTING", "VALIDATING", "STRESS_TESTING", "RISK_REVIEW", "PAPER_TRADING"):
        await builder.transition_lifecycle(strategy_id, "1.0.0", stage)

    with pytest.raises(StrategyLifecycleError):
        await builder.transition_lifecycle(strategy_id, "1.0.0", "APPROVED")

    result = await builder.transition_lifecycle(
        strategy_id, "1.0.0", "APPROVED", risk_warning_acknowledged=True
    )
    assert result.lifecycle_status == "APPROVED"
    assert result.risk_warning is not None


async def test_deploy_approval_no_warning_when_matching(pool):
    owner = await create_test_user(pool)
    await _set_risk_profile(pool, owner, "공격형")
    builder = StrategyBuilderService(pool)
    strategy_id = f"test-{uuid4().hex[:8]}"
    await builder.save_strategy(
        owner,
        strategy_id,
        "1.0.0",
        target_asset="BTC/USDT",
        market="crypto",
        exchange="bitget",
        fsm_definition={},
    )
    await _set_strategy_risk_level(pool, strategy_id, "1.0.0", "안정형")

    for stage in ("BACKTESTING", "VALIDATING", "STRESS_TESTING", "RISK_REVIEW", "PAPER_TRADING"):
        await builder.transition_lifecycle(strategy_id, "1.0.0", stage)

    result = await builder.transition_lifecycle(strategy_id, "1.0.0", "APPROVED")
    assert result.lifecycle_status == "APPROVED"
    assert result.risk_warning is None


# ---------- 훅③ ApprovalMode 변경(ApprovalSettingsService.update) ----------


async def test_solo_mode_warns_for_conservative_user(pool):
    user_id = await create_test_user(pool)
    await _set_risk_profile(pool, user_id, "안정형")
    service = ApprovalSettingsService(pool)

    with pytest.raises(ApprovalSettingsError):
        await service.update(user_id, mode="SOLO")

    settings = await service.update(user_id, mode="SOLO", risk_warning_acknowledged=True)
    assert settings.mode == "SOLO"
    assert settings.risk_warning is not None


async def test_dual_mode_no_warning_for_conservative_user(pool):
    user_id = await create_test_user(pool)
    await _set_risk_profile(pool, user_id, "안정형")
    service = ApprovalSettingsService(pool)

    settings = await service.update(
        user_id, mode="DUAL", second_approver_contact="backup@example.com"
    )
    assert settings.risk_warning is None


async def test_solo_mode_no_warning_for_aggressive_user(pool):
    user_id = await create_test_user(pool)
    await _set_risk_profile(pool, user_id, "공격형")
    service = ApprovalSettingsService(pool)

    settings = await service.update(user_id, mode="SOLO")
    assert settings.risk_warning is None
