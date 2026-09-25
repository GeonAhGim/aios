"""02b_bitget_api_v2_full_spec_v1.md sec5.1/5.3/7 -- BitgetAdapter remaining
public (mix/public) GET methods.

Spec: 02b_bitget_api_v2_full_spec_v1.md sec5.1/5.3/7

2026-09-25 task-6797(P6.line_cap) -- split out of `futures_market_mixin.py`
because adding these would exceed the 300-line cap (pure move, no
behavior change).

Endpoints (best-effort from community SDK reference, unverified live):
- GET /api/v2/mix/market/fills
- GET /api/v2/mix/market/fills-history
- GET /api/v2/mix/position/adlRank
- GET /api/v2/public/annoucements
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.data.models.market_data import PublicTrade
from src.exchanges.bitget.futures_market_mixin import DEFAULT_PRODUCT_TYPE
from src.exchanges.bitget.symbols import to_bitget_symbol as _to_bitget_symbol
from src.exchanges.common.http_client import SignedRequestClient


def _row_to_public_trade(row: dict[str, Any], symbol: str) -> PublicTrade:
    return PublicTrade(
        symbol=symbol,
        exchange="bitget",
        trade_id=row["tradeId"],
        price=Decimal(row["price"]),
        quantity=Decimal(row["size"]),
        side=row.get("side", ""),
        timestamp=datetime.fromtimestamp(int(row["ts"]) / 1000, tz=timezone.utc),
    )


class BitgetFuturesMarketExtraMixin:
    async def get_futures_public_trades(
        self: SignedRequestClient,
        symbol: str,
        *,
        limit: int = 100,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[PublicTrade]:
        """Reuses the `PublicTrade` model, same as
        market_data_mixin.py::get_public_trades (spot)."""
        raw = await self._request(
            "GET",
            "/api/v2/mix/market/fills",
            params={
                "symbol": _to_bitget_symbol(symbol),
                "productType": product_type,
                "limit": str(limit),
            },
        )
        return [_row_to_public_trade(row, symbol) for row in raw["data"]]

    async def get_futures_public_trades_history(
        self: SignedRequestClient,
        symbol: str,
        *,
        limit: int = 100,
        end_time: str | None = None,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[PublicTrade]:
        params: dict[str, Any] = {
            "symbol": _to_bitget_symbol(symbol),
            "productType": product_type,
            "limit": str(limit),
        }
        if end_time is not None:
            params["endTime"] = end_time
        raw = await self._request("GET", "/api/v2/mix/market/fills-history", params=params)
        return [_row_to_public_trade(row, symbol) for row in raw["data"]]

    async def get_futures_adl_rank(
        self: SignedRequestClient,
        *,
        symbol: str | None = None,
        product_type: str = DEFAULT_PRODUCT_TYPE,
    ) -> list[dict[str, Any]]:
        """Returns raw dicts until a consumer needs a typed model, same
        judgment as get_futures_position_lever_tiers."""
        params: dict[str, Any] = {"productType": product_type}
        if symbol is not None:
            params["symbol"] = _to_bitget_symbol(symbol)
        raw = await self._request("GET", "/api/v2/mix/position/adlRank", params=params)
        return list(raw["data"])

    async def get_public_announcements(
        self: SignedRequestClient,
        *,
        ann_type: str = "latest_news",
    ) -> list[dict[str, Any]]:
        """Returns raw dicts; no consumer exists yet."""
        raw = await self._request(
            "GET", "/api/v2/public/annoucements", params={"annType": ann_type}
        )
        return list(raw["data"].get("annList") or [])
