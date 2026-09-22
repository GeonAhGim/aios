"""2.9 / 2.10 / 2.11 / 2.14 — Trading models.

Spec: 01_data_models_v1.4.md#§1.4, 11_implementation_rules_v1.2.md#§11.1
(Money type replaces amount fields to prevent multi-exchange/currency summation errors),
01_data_models_v1.4.md#§1.0 (multi-asset-class expansion, ADR-2026-08-28)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, SecretStr

from src.data.models.base import AssetClass, Money, OptionType


class OrderStatus(str, Enum):
    """8.3 Order State Machine 1:1 구현"""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    # task-2432 (L4-06 §9): promoted from a kill-switch-only literal written
    # by src/services/safety/open_order_sweeper.py -- previously outside this
    # frozen contract (§3.3), which forced that module into a self-loop event
    # workaround to avoid crashing replay_verify (see its module docstring).
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    UNKNOWN = "UNKNOWN"  # 8.3 principle: do not assume UNKNOWN means failure


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    # TWAP/VWAP etc. are implemented as split orders in the 8.3-A
    # Execution Strategy layer (not individual Order types)


class Order(BaseModel):
    order_id: UUID = Field(default_factory=uuid4)
    exchange_order_id: str | None = None  # 7.5 주문 멱등성 — 거래소측 ID 별도 추적
    client_order_id: str  # Idempotency key — use the same value on retransmission
    strategy_id: str
    strategy_version: str
    execution_id: int | None = None
    # ADR-2026-08-10-C — FD-16 (strategy execution dashboard): track which
    # execution instance (strategy_executions.id) this order belongs to. None
    # is allowed: FD-8 FROZEN decision layer may call directly in
    # test/simulation paths without execution context.
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

    # 8.6-A-2 Anti-Front-Running — slice info for liquidation execution (None for normal orders)
    execution_slice_id: str | None = None
    is_liquidation: bool = False

    # ADR-2026-08-28 multi-asset-class expansion — asset_class is required
    # (no default) to prevent silent misclassification. Others are filled
    # only for options/futures orders; crypto/spot/stock orders stay None.
    asset_class: AssetClass
    option_type: OptionType | None = None
    strike_price: Decimal | None = None
    expiry_date: date | None = None
    contract_multiplier: Decimal | None = None
    underlying_symbol: str | None = None


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

    # ADR-2026-08-28 multi-asset-class expansion — same principle as Order.
    asset_class: AssetClass
    option_type: OptionType | None = None
    strike_price: Decimal | None = None
    expiry_date: date | None = None
    contract_multiplier: Decimal | None = None
    underlying_symbol: str | None = None


class FuturesContractInfo(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §8 — perpetual futures contract spec
    (contract unit / min order quantity / max leverage). Prepared so the
    validator (03 §3.3) can compare order parameters against these values
    for schema validation — no current consumer (17.9-A, wire when needed)."""

    symbol: str
    exchange: str
    base_coin: str
    quote_coin: str
    min_order_size: Decimal
    price_tick_size: Decimal
    size_tick_size: Decimal
    max_leverage: Decimal


class SecretBundle(BaseModel):
    """FD-1.1/03 §3.1 (Loader.load_env_secrets) return type.
    1:1 mapping with the full list in 07 §7.3 `.env.example`.

    Reflects Red Team audit (docs/RED_TEAM_FINDINGS.md #10) — masking via
    `__repr__`/`__str__` alone was bypassed by `model_dump()`/model_dump_json()`
    (the serialization path FastAPI actually uses), returning plaintext.
    All actual credential/key fields are declared as `SecretStr` to ensure
    consistent masking across all Pydantic serialization paths — call
    `.get_secret_value()` only where the secret is actually needed."""

    database_url: SecretStr
    jwt_secret_key: SecretStr
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    # FD-12.1 shared: exchange credentials + FD-11.5 withdrawal whitelist encryption
    credential_encryption_key: SecretStr
    bitget_api_key: SecretStr
    bitget_api_secret: SecretStr
    kis_app_key: SecretStr
    kis_app_secret: SecretStr
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    fcm_server_key: SecretStr | None = None
    apns_key_id: SecretStr | None = None
    # FD-17 frontend (doc 17, apps/web): local dev server runs on a separate
    # origin (Vite, default 5173) and needs CORS — not a secret but this
    # bundle is the single source of truth for the full .env config.
    cors_allowed_origins: list[str] = Field(default_factory=list)

    def __repr__(self) -> str:
        """07 §7.1 masking principle — no field exposed in plaintext (SecretStr
        already guarantees it, but this override maintains compact display)."""
        return f"SecretBundle(<{len(self.__class__.model_fields)} fields, masked>)"

    __str__ = __repr__


class AccountBalance(BaseModel):
    """v1.4 (ADR-2026-08-28) — discovered during real Bitget balance API
    investigation: this model must NOT use Money(currency: Currency). The
    Currency enum only defines USDT/KRW (settlement/display currencies), but
    actual balances hold arbitrary coins like BTC/ETH/SOL which cannot be
    expressed as Currency. The asset field already handles "what quantity" so
    Decimal is used directly (see 01 §1.4 comment)."""

    exchange: str
    asset: str
    total: Decimal
    available: Decimal
    used_margin: Decimal = Decimal("0")
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class MarginAccountAsset(BaseModel):
    """02b_bitget_api_v2_full_spec_v1.md §8 — margin account assets, liabilities,
    and risk rate. Separate from AccountBalance because margin's core concepts
    — "borrowed money" (borrowed/interest) and "headroom to liquidation"
    (risk_rate) — do not exist in spot balances. Forcing a fit would mix two
    domains (the opposite direction of 17.9-A anti-overdesign — here, forced
    reuse would be under-design)."""

    exchange: str
    margin_type: str  # "crossed" | "isolated"
    symbol: str | None = None  # isolated has per-symbol; crossed is None
    coin: str
    available: Decimal
    borrowed: Decimal
    interest: Decimal
    net_asset: Decimal
    risk_rate: Decimal | None = None  # FD-8.3 candidate input for liquidation risk judgment
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
