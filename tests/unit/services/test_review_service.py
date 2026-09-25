"""13.9 review_service.list_reviews 단위테스트 (task-3459).

review_service.py:89 list_reviews가 listing 존재를 검증하지 않고 상시 200
빈배열을 반환하던 문제(리뷰 3330, task-407/724/3330에서 3회 반복 확인)의
분기 로직을 실DB 없이 고정한다. 실DB 시나리오(구매 흐름 포함)는
tests/integration/test_review_service.py, HTTP 계약은
tests/integration/test_marketplace_router.py에 있다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.api.contracts.error_codes import HTTP_STATUS, ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.services.review_service import Review, ReviewError, ReviewNotFoundError, ReviewService

LISTING_ID = 42


def _review_row(listing_id: int = LISTING_ID) -> dict[str, object]:
    return {
        "id": 1,
        "listing_id": listing_id,
        "reviewer_user_id": uuid4(),
        "rating": 5,
        "comment": "좋음",
        "created_at": datetime.now(timezone.utc),
    }


class _FakeConnection:
    """SELECT 1 FROM strategy_listings(존재 확인) → SELECT * FROM reviews 순서를
    호출 기록으로 남긴다 — 존재하지 않으면 두 번째 쿼리가 실행되지 않아야 한다."""

    def __init__(self, *, listing_exists: bool, review_rows: list[dict[str, object]]) -> None:
        self._listing_exists = listing_exists
        self._review_rows = review_rows
        self.fetchval_calls = 0
        self.fetch_calls = 0

    async def fetchval(self, query: str, *args: object) -> int | None:
        self.fetchval_calls += 1
        return 1 if self._listing_exists else None

    async def fetch(self, query: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls += 1
        return self._review_rows


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def test_list_reviews_raises_not_found_when_listing_missing() -> None:
    conn = _FakeConnection(listing_exists=False, review_rows=[])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    with pytest.raises(ReviewNotFoundError):
        await service.list_reviews(LISTING_ID)


async def test_list_reviews_raises_not_found_for_nonpositive_listing_id() -> None:
    """경계값 — 존재할 수 없는 listing_id(0)도 동일하게 404로 처리된다."""
    conn = _FakeConnection(listing_exists=False, review_rows=[])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    with pytest.raises(ReviewNotFoundError):
        await service.list_reviews(0)


async def test_list_reviews_ignores_orphaned_review_rows_for_missing_listing() -> None:
    """failure injection — listing이 지워진 뒤에도 orphan `reviews` 행이 남아있는
    상태(FK 정합성 규정이 깨진 시나리오)를 주입한다. listing 존재 확인이
    먼저 실패하면 두 번째 쿼리(reviews 조회) 자체가 실행되지 않아야 하며,
    orphan 데이터가 새어나가서는 안 된다(fail-closed)."""
    conn = _FakeConnection(listing_exists=False, review_rows=[_review_row()])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    with pytest.raises(ReviewNotFoundError):
        await service.list_reviews(LISTING_ID)

    assert conn.fetchval_calls == 1
    assert conn.fetch_calls == 0  # reviews 쿼리는 절대 실행되지 않아야 한다


async def test_list_reviews_returns_empty_list_when_listing_has_no_reviews() -> None:
    conn = _FakeConnection(listing_exists=True, review_rows=[])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    result = await service.list_reviews(LISTING_ID)

    assert result == []


async def test_list_reviews_returns_reviews_when_listing_has_reviews() -> None:
    conn = _FakeConnection(listing_exists=True, review_rows=[_review_row()])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    result = await service.list_reviews(LISTING_ID)

    assert len(result) == 1
    assert isinstance(result[0], Review)
    assert result[0].listing_id == LISTING_ID


@pytest.mark.perf
async def test_list_reviews_perf_budget_for_1000_rows() -> None:
    """perf 회귀 방어 — 1000건 조회 시 pydantic 변환 포함 200ms 예산."""
    rows = [_review_row() for _ in range(1000)]
    conn = _FakeConnection(listing_exists=True, review_rows=rows)
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    start = time.perf_counter()
    result = await service.list_reviews(LISTING_ID)
    elapsed_ms = (time.perf_counter() - start) * 1000

    assert len(result) == 1000
    assert elapsed_ms < 200, f"list_reviews(1000 rows)={elapsed_ms:.1f}ms, 예산 200ms 초과"


def test_review_not_found_maps_to_resource_not_found_404() -> None:
    """gate-red repro — exception_registry.py에서 ReviewNotFoundError가
    ReviewError보다 먼저 등록돼야 한다. 순서가 뒤집히면(부모가 먼저 매치)
    이 테스트가 VALIDATION_INVALID_FIELD/400을 받아 즉시 실패한다."""
    code, _message, _details = map_exception(ReviewNotFoundError("존재하지 않는 리스팅입니다."))

    assert code == ErrorCode.RESOURCE_NOT_FOUND
    assert HTTP_STATUS[code] == 404


def test_review_error_base_still_maps_to_validation_400() -> None:
    """ReviewNotFoundError 등록이 기존 ReviewError(400) 매핑을 깨지 않았는지
    확인한다 — create_review 쪽 회귀 방지."""
    code, _message, _details = map_exception(ReviewError("rating은 1~5 사이여야 합니다."))

    assert code == ErrorCode.VALIDATION_INVALID_FIELD
    assert HTTP_STATUS[code] == 400
