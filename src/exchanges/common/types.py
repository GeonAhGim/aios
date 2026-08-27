"""6.1 — TickerCallback 타입 + ExchangeCapability + MarketHours.

Spec: 02_exchange_adapter_v1.2.md#§2.1, §2.0-A (다자산군 확장, ADR-2026-08-28)
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

from pydantic import BaseModel, Field

from src.data.models.base import AssetClass
from src.data.models.market_data import Ticker

TickerCallback = Callable[[Ticker], Awaitable[None]]


class MarketHours(BaseModel):
    """8.6-A Cross-Asset Time-Gap Buffer 판단에 사용."""

    timezone: str
    open_time: str
    close_time: str
    trading_days: list[str] = Field(default_factory=list)


class ExchangeCapability(BaseModel):
    """7.6 Capability Model — 각 Adapter가 스스로 선언하는 지원 기능.

    v1.4(ADR-2026-08-28) — supported_asset_classes는 실제로 이 Adapter가
    거래 가능한 자산군 전체를 담는다(02번 §2.0-A capability-gated 원칙의
    근거 데이터). Validator가 주문의 asset_class를 이 목록과 대조해 미지원
    조합을 거부한다.
    """

    exchange_name: str
    supported_asset_classes: list[AssetClass]
    supports_spot: bool
    supports_futures: bool
    supports_options: bool = False
    supports_leverage: bool
    supports_websocket: bool
    max_leverage: Decimal
    reference_feed_coverage: str  # "high" | "medium" | "low"
    has_official_sandbox: bool
    market_hours: MarketHours | None = None
    min_order_size: dict[str, Decimal] = Field(default_factory=dict)
    tick_size: dict[str, Decimal] = Field(default_factory=dict)
