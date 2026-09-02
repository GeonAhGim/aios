"""13.8 — 리스팅 검색·정렬 API.

Spec: 기능설계문서_v1.20.md#FD-13.8, 14번 문서 §14.4

기본 정렬(sort_by="RECOMMENDED")은 리스팅 생성일이 아니라 검증통과일
(verified_at, FD-13.2 완료 시각) 역순 — 판매자가 재등록으로 상단 노출을
조작하는 것을 막는다(검증통과일은 검증담당자를 거쳐야만 갱신되므로
조작이 어렵다). 동점 시 2차 정렬은 샤프비율 내림차순(NULL은 항상 마지막).

편차(범위 축소): `min_backtest_months` 필터는 이 leaf에서 구현하지
않는다 — 실제 백테스트 기간을 추적하는 데이터 소스가 시스템 어디에도
없다(FD-16/백테스트 엔진 스콥 밖). 존재하지 않는 것을 거짓으로 필터링
하는 대신, 데이터 소스가 생기기 전까지 파라미터 자체를 받지 않는다
(12.2의 "출금권한 미지원 시 정직하게 경고"와 같은 원칙).

편차(2026-09-02, 사용자 요청): ZuluTrade의 ZuluRank(성과 기반 판매자
랭킹)에 대응하는 sort_by="SHARPE_RATIO" 옵션을 추가한다 — 순수 샤프
비율 내림차순(NULL 마지막)으로, 검증통과일과 무관하게 "성과가 좋은
전략"만 보고 싶은 사용자를 위한 명시적 선택지다."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import asyncpg
from pydantic import BaseModel

DEFAULT_PAGE_SIZE = 20

_ORDER_BY_SQL = {
    "RECOMMENDED": "l.verified_at DESC NULLS LAST, l.sharpe_ratio DESC NULLS LAST",
    "SHARPE_RATIO": "l.sharpe_ratio DESC NULLS LAST, l.verified_at DESC NULLS LAST",
}


class ListingSummary(BaseModel):
    id: int
    strategy_id: str
    strategy_version: str
    seller_user_id: UUID
    seller_type: str
    price: Decimal | None
    verified_at: datetime | None
    sharpe_ratio: Decimal | None


class ListingSearchResult(BaseModel):
    items: list[ListingSummary]
    total: int
    page: int
    page_size: int


class ListingSearchService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def search(
        self,
        *,
        asset_class: str | None = None,
        exchange: str | None = None,
        max_price: Decimal | None = None,
        sort_by: str = "RECOMMENDED",
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ListingSearchResult:
        order_by = _ORDER_BY_SQL.get(sort_by, _ORDER_BY_SQL["RECOMMENDED"])
        conditions = ["l.status = 'LISTED'"]
        params: list[object] = []

        if asset_class is not None:
            params.append(asset_class)
            conditions.append(f"s.market = ${len(params)}")
        if exchange is not None:
            params.append(exchange)
            conditions.append(f"s.exchange = ${len(params)}")
        if max_price is not None:
            params.append(max_price)
            conditions.append(f"l.price <= ${len(params)}")

        where_clause = " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                f"SELECT COUNT(*) FROM strategy_listings l "
                f"JOIN strategies s ON s.strategy_id = l.strategy_id "
                f"AND s.version = l.strategy_version WHERE {where_clause}",
                *params,
            )

            limit_param = len(params) + 1
            offset_param = len(params) + 2
            rows = await conn.fetch(
                f"""
                SELECT l.id, l.strategy_id, l.strategy_version, l.seller_user_id,
                       l.seller_type, l.price, l.verified_at, l.sharpe_ratio
                FROM strategy_listings l
                JOIN strategies s ON s.strategy_id = l.strategy_id
                    AND s.version = l.strategy_version
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT ${limit_param} OFFSET ${offset_param}
                """,
                *params,
                page_size,
                (page - 1) * page_size,
            )

        return ListingSearchResult(
            items=[ListingSummary(**dict(row)) for row in rows],
            total=total,
            page=page,
            page_size=page_size,
        )
