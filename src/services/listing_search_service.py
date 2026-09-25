"""13.8 -- Listing search and sort API.

Spec: functional_design_v1.20.md#FD-13.8, document #14 §14.4

Default sort (sort_by="RECOMMENDED") orders by verification date
(verified_at, FD-13.2 completion time) descending -- not by listing
creation date -- to prevent sellers from gaming top placement via
re-registration (verification date only updates after review by a
verifier, making manipulation difficult). Ties are broken by Sharpe
ratio descending (NULL always last).

Deviation (scope reduction): `min_backtest_months` filter is not
implemented in this leaf -- no data source anywhere in the system tracks
actual backtest duration (outside FD-16/backtest engine scope). Instead
of filtering non-existent data to false, the parameter is not accepted
at all until such a data source exists (same principle as 12.2
"warn honestly when withdrawal authority is unsupported").

Deviation (2026-09-02, user request): Add sort_by="SHARPE_RATIO" option
corresponding to ZuluTrade's ZuluRank (performance-based seller ranking)
-- pure Sharpe ratio descending (NULL last), an explicit choice for users
who want to see only "high-performing strategies" regardless of
verification date."""

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
