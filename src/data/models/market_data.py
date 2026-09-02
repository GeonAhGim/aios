"""2.7 / 2.8 — Market Data 모델.

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


class FundingRate(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §8 — 무기한 선물 펀딩레이트.
    Ticker/Candle과 동일 계층(시장 공개 데이터)에 둔다 — Order/Position
    같은 계좌 상태가 아니라 시세 데이터의 일종."""

    symbol: str
    exchange: str
    current_rate: Decimal
    next_funding_time: datetime
    timestamp: datetime
