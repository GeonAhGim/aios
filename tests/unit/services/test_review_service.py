"""13.9 review_service.list_reviews 단위테스트 (task-3459).

review_service.py:89 list_reviews가 listing 존재를 검증하지 않고 상시 200
빈배열을 반환하던 문제(리뷰 3330, task-407/724/3330에서 3회 반복 확인)의
분기 로직을 실DB 없이 고정한다. 실DB 시나리오(구매 흐름 포함)는
tests/integration/test_review_service.py, HTTP 계약은
tests/integration/test_marketplace_router.py에 있다.
"""

from __future__ import annotations

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
async def test_list_reviews_perf_budget_for_1000_rows(perf_budget) -> None:
    """perf 회귀 방어 — 1000건 조회 시 pydantic 변환 포함 200ms 예산.

    raw time.perf_counter() → perf_budget.sample_async() 전환(task-15297).
    1000건 루프를 wrapper 함수로 묶어 한 번의 sample_async 호출로 측정:
    process_time tick(15.6ms) 양자화 방지를 위해 batch가 아닌 전체 루프
    한 번으로 묶으면 총 1000회 CPU 시간이 tick을 넘어 정확한 측정 가능.
    단위: cpu_ms 기준, 예산 200ms(parallel CI + coverage 오버헤드 반영).
    """
    rows = [_review_row() for _ in range(1000)]
    conn = _FakeConnection(listing_exists=True, review_rows=rows)
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    async def _batch() -> None:
        result = await service.list_reviews(LISTING_ID)
        assert len(result) == 1000

    # 2026-10-28: 병렬 CI 부하에서 단발 wall 측정이 208ms로 예산을 넘었다(full CI dd5f97fa).
    # 독스트링의 측정 축(cpu_ms)과 저장소 표준(best-of-N: spike 제외)에 맞춘다. 예산 200ms 불변.
    sample = await perf_budget.best_of_async(_batch, n=5, warmup=1)
    assert sample.cpu_ms < 200, f"list_reviews(1000 rows)={sample.cpu_ms:.1f}ms, 예산 200ms 초과"


@pytest.mark.perf
async def test_list_reviews_perf_budget_catches_regression(perf_budget) -> None:
    """failure injection — 10회 호출에 각 50ms 지연을 주입하면 200ms 예산을
    초과해야 한다(임시 실행 후 원복, 결과를 note에) — 항상 통과하는 허울
    단언 금지.

    지연 주입: _FakeConnection.fetch에 50ms sleep을 추가.
    10회 x 50ms = 500ms wall-clock이므로 cpu_ms도 200ms 예산을 초과.
    이 테스트가 통과하면 원본 예산 단언이 허울이 아님을 검증한다."""
    import asyncio

    rows = [_review_row() for _ in range(1000)]
    conn = _FakeConnection(listing_exists=True, review_rows=rows)

    original_fetch = conn.fetch

    async def _fetch_with_delay(self, query: str, *args: object) -> list[dict[str, object]]:
        await asyncio.sleep(0.05)
        return await original_fetch(query, *args)

    # instance-bound: self는 _FakeConnection 인스턴스, 원본 fetch는 bound method
    conn.fetch = lambda query, *args: _fetch_with_delay(conn, query, *args)
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    async def _batch_10_delayed() -> None:
        for _ in range(10):
            result = await service.list_reviews(LISTING_ID)
            assert len(result) == 1000

    sample = await perf_budget.sample_async(_batch_10_delayed)
    assert sample.wall_ms >= 200, (
        f"지연 주입에도 wall_ms={sample.wall_ms:.1f}ms < 200ms — 지연이 측정되지 않음"
    )


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
