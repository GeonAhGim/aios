"""KRX (Korea Exchange) data adapters via KIS Open API.

Provides three data sources required by RD-12:
- ``index``: index price and trend lookups
- ``investor``: investor-by-investor trend estimates (individual, foreign, institutions)
- ``short``: short-selling resistance stock screening

All adapters require a ``KISAdapter`` (or compatible) instance as ``client``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

# ---------------------------------------------------------------------------
# Domain models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IndexQuote:
    """Single index price snapshot."""

    code: str
    name: str
    price: float
    change: float
    change_rate: float
    high: float
    low: float
    volume: int
    observed_at: datetime


@dataclass(frozen=True)
class IndexTrend:
    """Trend series for an index."""

    code: str
    name: str
    points: list[IndexPoint]


@dataclass(frozen=True)
class IndexPoint:
    """One point in an index trend series."""

    date: str  # "YYYYMMDD"
    price: float
    change_rate: float


@dataclass(frozen=True)
class InvestorTrend:
    """Investor-by-investor trend estimate for a single date."""

    date: str
    individual_pct: float
    foreign_pct: float
    institution_pct: float
    etc_pct: float
    total_volume: int


@dataclass(frozen=True)
class ShortResistanceStock:
    """A stock flagged as short-selling resistance."""

    stock_code: str
    stock_name: str
    price: float
    short_volume: int
    total_volume: int
    short_ratio: float
    sector: str


# ---------------------------------------------------------------------------
# Client protocol — duck-type against KISAdapter
# ---------------------------------------------------------------------------


class KISClient(Protocol):
    """Minimal interface expected from the KIS adapter."""

    _is_paper_trading: bool

    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------


class KRXIndexAdapter:
    """Index price + trend data from KIS ``domestic_stock_03_mixin`` endpoints."""

    def __init__(self, client: KISClient) -> None:
        self._client = client

    async def get_index_price(self, code: str) -> IndexQuote:
        """Fetch current price for a single index.

        Maps to ``inquire_index_itemprice`` (INDI000R).
        """
        raw = await self._client._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-index-price",
            "INDI000R",
        )
        row = raw.get("output", {})
        if not row:
            raise ValueError(f"Index {code} not found in KIS response")
        return IndexQuote(
            code=row.get("indicode", code),
            name=row.get("inpct_name", ""),
            price=float(row.get("prdy_vrss", 0)),
            change=float(row.get("prdy_crt", 0)),
            change_rate=float(row.get("stdc_crt", 0)),
            high=float(row.get("hts_hgpr", 0)),
            low=float(row.get("hts_lwpr", 0)),
            volume=int(row.get("hts_vol", 0)),
            observed_at=datetime.now(timezone.utc),
        )

    async def get_index_trend(
        self,
        code: str,
        days: int = 30,
    ) -> IndexTrend:
        """Fetch trend series for an index.

        Maps to ``inquire_trend_index`` (FHKST030201R).
        """
        raw = await self._client._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-trend-index",
            "FHKST030201R",
            params={
                "INDI_CODE": code,
                "INDI_CODE_TYPE": "1",  # 1=main index
                "NUM_COUNT": str(days),
            },
        )
        points_raw = raw.get("output1", [])
        points = [
            IndexPoint(
                date=p.get("indidate", ""),
                price=float(p.get("indiprice", 0)),
                change_rate=float(p.get("indichg", 0)),
            )
            for p in points_raw
        ]
        # Use first point's name as fallback
        name = ""
        if points:
            name = points_raw[0].get("inpct_name", "")
        return IndexTrend(
            code=code,
            name=name,
            points=points,
        )


class KRXInvestorAdapter:
    """Investor-by-investor trend data from KIS endpoints.

    Maps to ``inquire_trend_investor`` (FHKST030301R).
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    async def get_investor_trend(
        self,
        date: str | None = None,
    ) -> InvestorTrend:
        """Fetch investor-by-investor trend for a date.

        ``date`` format: "YYYYMMDD". If omitted, uses today.
        """
        if date is None:
            date = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._client._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-trend-investor",
            "FHKST030301R",
            params={
                "DATE": date,
            },
        )
        row = raw.get("output", {})
        if not row:
            raise ValueError(f"No investor data for {date} in KIS response")
        return InvestorTrend(
            date=date,
            individual_pct=float(row.get("invst_pct", 0)),
            foreign_pct=float(row.get("frgn_by_pct", 0)),
            institution_pct=float(row.get("inst_pct", 0)),
            etc_pct=float(row.get("etc_pct", 0)),
            total_volume=int(row.get("invst_volume", 0)),
        )


class KRXShortAdapter:
    """Short-selling resistance stock screening.

    Maps to ``inquire_top_short_resistance`` (FHKUP080700R).
    """

    def __init__(self, client: KISClient) -> None:
        self._client = client

    async def get_short_resistance_stocks(
        self,
        limit: int = 10,
    ) -> list[ShortResistanceStock]:
        """Fetch top short-selling resistance stocks.

        ``limit`` caps the number of returned stocks.
        """
        raw = await self._client._request(
            "GET",
            "/uapi/domestic-stock/v1/quotations/inquire-top-short-resistance",
            "FHKUP080700R",
            params={
                "LIMIT_COUNT": str(limit),
            },
        )
        rows = raw.get("output1", [])
        result = []
        for row in rows[:limit]:
            short_vol = int(row.get("stlt_qty", 0))
            total_vol = int(row.get("qttr_qty", 0))
            ratio = (short_vol / total_vol * 100) if total_vol else 0.0
            result.append(
                ShortResistanceStock(
                    stock_code=row.get("ispr_code", ""),
                    stock_name=row.get("ispr_name", ""),
                    price=float(row.get("ispr_px", 0)),
                    short_volume=short_vol,
                    total_volume=total_vol,
                    short_ratio=ratio,
                    sector=row.get("scls_name", ""),
                )
            )
        return result
