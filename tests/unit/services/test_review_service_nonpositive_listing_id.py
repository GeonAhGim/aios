"""13.9 review_service.list_reviews — nonpositive listing_id 경계값 테스트.

split from test_review_service.py (task-20511): pytest_perf_parallel_2 병렬 실행에서
perf 테스트와 CPU 경합을 피하기 위해 경계값 테스트를 분리한다.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.review_service import ReviewNotFoundError, ReviewService

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


async def test_list_reviews_raises_not_found_for_nonpositive_listing_id() -> None:
    """경계값 — 존재할 수 없는 listing_id(0)도 동일하게 404로 처리된다."""
    conn = _FakeConnection(listing_exists=False, review_rows=[])
    service = ReviewService(cast(asyncpg.Pool, _FakePool(conn)))

    with pytest.raises(ReviewNotFoundError):
        await service.list_reviews(0)
