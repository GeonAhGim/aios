"""2.9 / 2.10 / 2.11 — Trading 모델.

Spec: 01_data_models_v1.3.md#§1.4, 11_implementation_rules_v1.2.md#§11.1
(금액 필드는 Money로 교체 — 다중 거래소/다중 통화 합산 오류 방지)
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.data.models.base import Money


class OrderStatus(str, Enum):
    """8.3 Order State Machine 1:1 구현"""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 8.3 원칙: UNKNOWN을 실패로 단정하지 않는다


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    # TWAP/VWAP 등은 8.3-A Execution Strategy 계층에서 분할 주문으로 구현(단일 Order 타입 아님)


class Order(BaseModel):
    order_id: UUID = Field(default_factory=uuid4)
    exchange_order_id: str | None = None  # 7.5 주문 멱등성 — 거래소측 ID 별도 추적
    client_order_id: str  # 멱등성 키. 재전송 시에도 동일 값 사용
    strategy_id: str
    strategy_version: str
    execution_id: int | None = None
    # ADR-2026-08-10-C — FD-16(전략 실행 제어판)에서 이 주문이 어느 실행
    # 인스턴스(strategy_executions.id) 소속인지 추적. None 허용: FD-8 FROZEN
    # 판단 계층이 실행 컨텍스트 없이 직접 호출되는 테스트/시뮬레이션 경로가 있을 수 있다.
    symbol: str
    exchange: str
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    price: Money | None = None  # MARKET 주문은 None
    status: OrderStatus = OrderStatus.CREATED
    filled_quantity: Decimal = Decimal("0")
    average_fill_price: Money | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # 8.6-A-2 Anti-Front-Running — 청산 실행 시 슬라이스 정보 (일반 주문은 None)
    execution_slice_id: str | None = None
    is_liquidation: bool = False


class Position(BaseModel):
    symbol: str
    exchange: str
    strategy_id: str
    execution_id: int | None = None  # ADR-2026-08-10-C, Order와 동일 근거
    quantity: Decimal
    average_entry_price: Money
    current_price: Money
    unrealized_pnl: Money
    realized_pnl: Money
    leverage: Decimal = Decimal("1")
    margin: Money | None = None
    entry_time: datetime
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SecretBundle(BaseModel):
    """FD-1.1/03번 §3.1(Loader.load_env_secrets)가 반환하는 타입.
    07번 §7.3 `.env.example` 전체 목록과 1:1 대응."""

    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    # FD-12.1 거래소 자격증명 + FD-11.5 출금 화이트리스트 암호화 공용
    credential_encryption_key: str
    bitget_api_key: str
    bitget_api_secret: str
    kis_app_key: str
    kis_app_secret: str
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    fcm_server_key: str | None = None
    apns_key_id: str | None = None

    def __repr__(self) -> str:
        """07번 §7.1 마스킹 원칙 — 어떤 필드도 평문 노출 금지."""
        return f"SecretBundle(<{len(self.__class__.model_fields)} fields, masked>)"

    __str__ = __repr__


class AccountBalance(BaseModel):
    exchange: str
    asset: str
    total: Money
    available: Money
    used_margin: Money
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
