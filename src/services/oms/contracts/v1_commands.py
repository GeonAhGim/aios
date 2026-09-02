"""OMS 명령 DTO(L4 명세 §3.1).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.1, §9 L4-01.

107번 §3 계약 버전 규칙 — L2 도메인 계약, `schema_version="v1"`. optional
필드 추가는 MINOR(버전 불변). `Order`/`OrderStatus`(01번) 자체는 공유접점
§2.3 동결 계약이라 여기서 재정의하지 않고 그대로 참조한다(§3.3).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType

SCHEMA_VERSION: Literal["v1"] = "v1"


class IdempotencyScope(BaseModel):
    tenant_id: UUID
    account_ref: str
    provider: str  # "bitget" | "kis" | "nh" | "paper_sim"
    strategy_id: str
    strategy_version: str
    execution_id: int
    intent_seq: int  # 실행 내 단조 증가(FSM 전이 카운터)
    window_start: datetime  # 의도 시각을 window(기본 60s)로 내림
    schema_version: Literal["v1"] = SCHEMA_VERSION


class SubmitOrderCommand(BaseModel):
    command_id: UUID
    trace_id: UUID
    scope: IdempotencyScope
    symbol: str  # 정규 "BTC/USDT" / "005930"
    side: OrderSide
    order_type: OrderType  # MARKET|LIMIT (TWAP 등은 AlgoRequest)
    quantity: Decimal = Field(gt=0)
    price: Decimal | None = None
    time_in_force: Literal["GTC", "IOC", "FOK", "DAY"] = "GTC"
    asset_class: AssetClass
    mode: Literal["PAPER"] = "PAPER"  # LIVE 값 자체를 계약에서 배제(§4.3 I9)
    parent_order_id: UUID | None = None
    algo_run_id: UUID | None = None
    is_liquidation: bool = False
    actor_subject_id: UUID | Literal["system"]
    issued_at: datetime


class CancelOrderCommand(BaseModel):
    command_id: UUID
    trace_id: UUID
    order_id: UUID
    tenant_id: UUID
    reason: str
    actor_subject_id: UUID | Literal["system"]
    issued_at: datetime


class ModifyOrderCommand(CancelOrderCommand):
    new_price: Decimal | None = None
    new_quantity: Decimal | None = None  # 둘 중 하나 필수(애플리케이션 계층에서 검증)


class AlgoRequest(BaseModel):
    algo_run_id: UUID
    trace_id: UUID
    scope: IdempotencyScope
    algo: Literal["TWAP", "VWAP", "POV", "ICEBERG"]
    symbol: str
    side: OrderSide
    total_quantity: Decimal
    start_at: datetime
    end_at: datetime
    slice_count: int = Field(ge=1, le=500)
    max_participation_pct: Decimal = Decimal("10")  # 슬라이스 qty ≤ 구간 예상 거래량 × pct
    size_jitter_pct: Decimal = Decimal("20")
    time_jitter_pct: Decimal = Decimal("30")
    display_quantity: Decimal | None = None  # ICEBERG만
    limit_price: Decimal | None = None
    seed: int  # 재현 가능한 무작위화
