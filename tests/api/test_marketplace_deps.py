"""src/api/marketplace_deps.py — 13번대 마켓플레이스 서비스 팩토리 Depends 커버리지.

전부 순수 조립 함수(생성자만 호출, I/O 없음)라 실DB 없이 단위테스트 가능.
get_listing_service/get_purchase_service가 functools.partial로 바인딩하는
check_paper_trading_eligibility/check_purchase_risk_warning 콜백은 partial
바인딩을 거쳐도 원래 fail-closed 계약(전자는 예외 흡수 후 False, 후자는
RiskMatchingError·DB 예외를 그대로 전파)이 유지되는지 실제로 호출해 검증한다."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.api import marketplace_deps
from src.services.dispute_service import DisputeService
from src.services.listing_search_service import ListingSearchService
from src.services.listing_service import ListingService
from src.services.purchase_service import PurchaseService
from src.services.review_service import ReviewService
from src.services.risk_matching import RiskMatchingError
from src.services.strategy_access_service import StrategyAccessService
from src.services.verification_service import VerificationService


def _pool() -> MagicMock:
    return MagicMock(name="pool")


def _event_bus() -> MagicMock:
    return MagicMock(name="event_bus")


def _pool_raising_on_acquire(exc: Exception) -> MagicMock:
    pool = MagicMock(name="pool")

    @asynccontextmanager
    async def _acquire():
        raise exc
        yield  # pragma: no cover

    pool.acquire = _acquire
    return pool


def _pool_with_conn(conn: MagicMock) -> MagicMock:
    pool = MagicMock(name="pool")

    @asynccontextmanager
    async def _acquire():
        yield conn

    pool.acquire = _acquire
    return pool


def test_get_listing_service_wires_pool():
    pool = _pool()
    service = marketplace_deps.get_listing_service(pool)
    assert isinstance(service, ListingService)
    assert service._pool is pool


def test_get_verification_service_wires_pool_and_publish():
    pool = _pool()
    event_bus = _event_bus()
    service = marketplace_deps.get_verification_service(pool, event_bus)
    assert isinstance(service, VerificationService)
    assert service._pool is pool
    assert service._publish is event_bus.publish


def test_get_listing_search_service_wires_pool():
    pool = _pool()
    service = marketplace_deps.get_listing_search_service(pool)
    assert isinstance(service, ListingSearchService)
    assert service._pool is pool


def test_get_purchase_service_wires_pool_and_publish():
    pool = _pool()
    event_bus = _event_bus()
    service = marketplace_deps.get_purchase_service(pool, event_bus)
    assert isinstance(service, PurchaseService)
    assert service._pool is pool
    assert service._publish is event_bus.publish


def test_get_strategy_access_service_wires_pool():
    pool = _pool()
    service = marketplace_deps.get_strategy_access_service(pool)
    assert isinstance(service, StrategyAccessService)
    assert service._pool is pool


def test_get_review_service_wires_pool():
    pool = _pool()
    service = marketplace_deps.get_review_service(pool)
    assert isinstance(service, ReviewService)
    assert service._pool is pool


def test_get_dispute_service_wires_pool():
    pool = _pool()
    service = marketplace_deps.get_dispute_service(pool)
    assert isinstance(service, DisputeService)
    assert service._pool is pool


async def test_get_listing_service_eligibility_callback_fails_closed_on_db_error():
    """failure-injection: pool.acquire가 예외를 내도 partial로 바인딩된
    check_paper_trading_eligibility는 fail-closed 계약(False)을 지켜야 한다."""
    pool = _pool_raising_on_acquire(OSError("db down"))
    service = marketplace_deps.get_listing_service(pool)

    result = await service._verify_eligibility("STRAT-1", "1.0.0", uuid4())

    assert result is False


async def test_get_purchase_service_risk_callback_propagates_db_error():
    """negative: partial로 바인딩된 check_purchase_risk_warning은 DB 오류를
    흡수하지 않고 그대로 전파해야 한다 — 조용히 위험 경고를 누락하면 안 된다."""
    pool = _pool_raising_on_acquire(RuntimeError("db down"))
    service = marketplace_deps.get_purchase_service(pool, _event_bus())

    with pytest.raises(RuntimeError, match="db down"):
        await service._check_risk_warning(uuid4(), "STRAT-1", "1.0.0")


async def test_get_purchase_service_risk_callback_rejects_missing_risk_profile():
    """negative: 위험등급이 지정되지 않은 사용자는 RiskMatchingError로 거부되어야
    한다 — partial 바인딩 이후에도 이 경계값 검증이 유지되는지 확인."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=None)
    pool = _pool_with_conn(conn)
    service = marketplace_deps.get_purchase_service(pool, _event_bus())

    with pytest.raises(RiskMatchingError):
        await service._check_risk_warning(uuid4(), "STRAT-1", "1.0.0")


async def test_get_purchase_service_risk_callback_wires_pool_via_partial():
    """negative: partial 바인딩이 인자 순서를 잘못 넘기면 asyncpg 대신 MagicMock의
    acquire가 정확한 pool 인스턴스로 호출되지 않는다 — 배선 정확성 확인."""
    conn = MagicMock()
    conn.fetchval = AsyncMock(side_effect=["안정형", "공격형"])
    pool = _pool_with_conn(conn)
    service = marketplace_deps.get_purchase_service(pool, _event_bus())

    warning = await service._check_risk_warning(uuid4(), "STRAT-1", "1.0.0")

    assert warning is not None
    assert conn.fetchval.await_count == 2
