"""13번대 — 마켓플레이스 서비스 팩토리 의존성."""
from __future__ import annotations

from functools import partial

import asyncpg
from fastapi import Depends

from src.core.event_bus.bus import EventBus
from src.services.dispute_service import DisputeService
from src.services.listing_search_service import ListingSearchService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.review_service import ReviewService
from src.services.risk_matching import check_purchase_risk_warning
from src.services.strategy_access_service import StrategyAccessService
from src.services.verification_service import VerificationService

from .deps import get_event_bus, get_pool


async def _always_eligible(strategy_id: str, version: str) -> bool:
    """Draft — FD-16 실행 이력을 실제로 추적하는 백테스트 엔진이 아직 없어
    (listing_service.py 자체 docstring 참조) 3개월 Paper Trading 이력
    검증을 통과시킨다. 실제 이력 추적이 생기면 이 콜백만 교체하면 된다."""
    return True


def get_listing_service(pool: asyncpg.Pool = Depends(get_pool)) -> ListingService:
    return ListingService(pool, verify_paper_trading_eligibility=_always_eligible)


def get_verification_service(
    pool: asyncpg.Pool = Depends(get_pool),
    event_bus: EventBus = Depends(get_event_bus),
) -> VerificationService:
    return VerificationService(pool, publish=event_bus.publish)


def get_listing_search_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> ListingSearchService:
    return ListingSearchService(pool)


def get_purchase_service(
    pool: asyncpg.Pool = Depends(get_pool),
    event_bus: EventBus = Depends(get_event_bus),
) -> PurchaseService:
    return PurchaseService(
        pool,
        check_risk_warning=partial(check_purchase_risk_warning, pool),
        publish=event_bus.publish,
    )


def get_strategy_access_service(
    pool: asyncpg.Pool = Depends(get_pool),
) -> StrategyAccessService:
    return StrategyAccessService(pool)


def get_review_service(pool: asyncpg.Pool = Depends(get_pool)) -> ReviewService:
    return ReviewService(pool)


def get_dispute_service(pool: asyncpg.Pool = Depends(get_pool)) -> DisputeService:
    return DisputeService(pool)
