"""13.9 — 리뷰 작성/조회 API.

Spec: 기능설계문서_v1.20.md#FD-13.9, 14번 문서 §14.2

구매 후 30일 이상 경과해야 작성 가능(14.2.3 조건 — 충분히 써본 뒤에만
평가하도록). 구매 이력이 있는 사용자만 작성 가능(자전거래 방지와 동일
원칙 — 판매자 본인 재구매로 리뷰 조작 차단), 리스팅당 1인 1리뷰(DB
UNIQUE 제약이 최종 방어선).

집계: 리뷰 5건 미만인 리스팅은 평균 별점 대신 "리뷰 부족" 표시 — 개별
리뷰 원문은 건수와 무관하게 항상 노출한다(별점만 신뢰도 임계치 적용).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from pydantic import BaseModel

MIN_DAYS_AFTER_PURCHASE = 30
MIN_REVIEWS_FOR_RATING = 5


class ReviewError(Exception):
    """FD-13.9 실패 — 라우터가 400/403으로 변환."""


class Review(BaseModel):
    id: int
    listing_id: int
    reviewer_user_id: UUID
    rating: int
    comment: str | None
    created_at: datetime


class RatingSummary(BaseModel):
    review_count: int
    average_rating: float | None  # MIN_REVIEWS_FOR_RATING 미만이면 None("리뷰 부족")


class ReviewService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_review(
        self,
        reviewer_user_id: UUID,
        listing_id: int,
        rating: int,
        *,
        comment: str | None = None,
    ) -> Review:
        if not 1 <= rating <= 5:
            raise ReviewError("rating은 1~5 사이여야 합니다.")

        async with self._pool.acquire() as conn:
            purchase = await conn.fetchrow(
                "SELECT purchased_at FROM strategy_purchases "
                "WHERE listing_id = $1 AND buyer_user_id = $2 "
                "ORDER BY purchased_at ASC LIMIT 1",
                listing_id,
                reviewer_user_id,
            )
            if purchase is None:
                raise ReviewError("이 리스팅에 대한 구매 이력이 없어 리뷰를 작성할 수 없습니다.")

            elapsed = datetime.now(timezone.utc) - purchase["purchased_at"]
            if elapsed < timedelta(days=MIN_DAYS_AFTER_PURCHASE):
                raise ReviewError(
                    f"구매 후 {MIN_DAYS_AFTER_PURCHASE}일이 지나야 리뷰를 작성할 수 있습니다."
                )

            try:
                row = await conn.fetchrow(
                    "INSERT INTO reviews (listing_id, reviewer_user_id, rating, comment) "
                    "VALUES ($1, $2, $3, $4) RETURNING *",
                    listing_id,
                    reviewer_user_id,
                    rating,
                    comment,
                )
            except asyncpg.UniqueViolationError as exc:
                raise ReviewError("이미 이 리스팅에 리뷰를 작성했습니다.") from exc

        return Review(**dict(row))

    async def list_reviews(self, listing_id: int) -> list[Review]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM reviews WHERE listing_id = $1 ORDER BY created_at DESC",
                listing_id,
            )
        return [Review(**dict(row)) for row in rows]

    async def get_rating_summary(self, listing_id: int) -> RatingSummary:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS cnt, AVG(rating) AS avg_rating FROM reviews "
                "WHERE listing_id = $1",
                listing_id,
            )
        count = row["cnt"]
        if count < MIN_REVIEWS_FOR_RATING:
            return RatingSummary(review_count=count, average_rating=None)
        return RatingSummary(review_count=count, average_rating=float(row["avg_rating"]))
