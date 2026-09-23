"""2.7 / 2.8 — Market Data models.

Spec: 01_data_models_v1.3.md#§1.3
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class Ticker(BaseModel):
    symbol: str
    exchange: str
    price: Decimal
    bid: Decimal
    ask: Decimal
    volume_24h: Decimal
    timestamp: datetime
    source_type: str  # "primary" | "reference" — 8.1-A 다중소스 교차검증용
    # ADR-2026-09-06-H D7 — for a quote received via a user-owned broker
    # connection, redistribution rights belong only to the connection's
    # owner. None means "this path hasn't tagged it yet," not "shareable" —
    # the logic that consumes this value to actually block sharing belongs
    # to the consumer (cache/screener, etc.), not to this field-adding leaf.
    redistribution_scope: str | None = None


class Candle(BaseModel):
    symbol: str
    exchange: str
    timeframe: str  # "1m", "5m", "1h" 등
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    open_time: datetime
    close_time: datetime


class OrderBookLevel(BaseModel):
    price: Decimal
    quantity: Decimal


class OrderBook(BaseModel):
    symbol: str
    exchange: str
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp: datetime


class SpotSymbolInfo(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §3.1/§8 — Symbol spec required by
    FD-4.1 (pre-validation). Bitget V2 does not provide tick/lot directly;
    it only supplies decimal places (pricePrecision/quantityPrecision), so
    tick_size/lot_size are computed as 10**-precision — live verification
    is needed to confirm the actual minimum increment matches exactly
    (some exchanges use non-standard ticks independent of precision)."""

    symbol: str
    exchange: str
    base_coin: str
    quote_coin: str
    tick_size: Decimal
    lot_size: Decimal
    min_trade_amount: Decimal
    status: str


class PublicTrade(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §3.1 — Market-wide trade stream
    (all trades on the symbol, not just my orders). For strengthening
    FD-2.6 (data reliability cross-validation) — unlike Ticker/Candle,
    this is raw per-trade granularity data."""

    symbol: str
    exchange: str
    trade_id: str
    price: Decimal
    quantity: Decimal
    side: str  # "buy" | "sell"
    timestamp: datetime


class FundingRate(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §8 — Perpetual futures funding rate.
    Placed at the same tier as Ticker/Candle (market public data) — not
    account state like Order/Position, but a type of market quote data."""

    symbol: str
    exchange: str
    current_rate: Decimal
    next_funding_time: datetime
    timestamp: datetime
