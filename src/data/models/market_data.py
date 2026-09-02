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


class SpotSymbolInfo(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §3.1/§8 — FD-4.1(사전검증)이
    필요로 하는 심볼 규격. Bitget V2는 tick/lot을 직접 내려주지 않고
    소수자리수(pricePrecision/quantityPrecision)만 제공하므로, tick_size/
    lot_size는 10**-precision으로 환산한 값이다 — 실제 최소 단위가 이
    값과 정확히 일치하는지는 라이브 검증 필요(일부 거래소는 precision과
    별개로 비표준 tick을 쓰기도 함)."""

    symbol: str
    exchange: str
    base_coin: str
    quote_coin: str
    tick_size: Decimal
    lot_size: Decimal
    min_trade_amount: Decimal
    status: str


class PublicTrade(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §3.1 — 시장 전체 체결 스트림(내
    주문이 아닌, 그 심볼에서 일어난 모든 체결). FD-2.6(데이터 신뢰도
    교차검증) 보강용 — Ticker/Candle과 달리 체결 단위 원시 데이터다."""

    symbol: str
    exchange: str
    trade_id: str
    price: Decimal
    quantity: Decimal
    side: str  # "buy" | "sell"
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
