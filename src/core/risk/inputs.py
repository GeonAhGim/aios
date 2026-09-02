"""L4_risk_and_safety_v1.0.md#3.2, #9 R-03 — `RiskInputs` typed snapshot.

기존 `RiskEngine.check(allocation, account_state: dict[str, Any])`는 입력이
느슨한 dict라서 조립 후 사라진다(재생 불가, R2 위반). 이 모듈은 그
dict를 규칙이 실제로 소비하는 값 전부를 갖춘 불변 스냅샷으로 대체한다.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, field_validator

from src.core.risk.hashing import canonical_json, sha256_hex
from src.core.risk.limits import ExposureLimit

_PCT_QUANTUM = Decimal("0.000001")


def _quantize_pct(value: Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return value.quantize(_PCT_QUANTUM)


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    return value


class _AsOfModel(BaseModel, frozen=True):
    """`as_of: datetime` 필드 + tz-aware 강제를 공유하는 베이스."""

    as_of: datetime

    @field_validator("as_of")
    @classmethod
    def _as_of_aware(cls, value: datetime) -> datetime:
        return _require_aware_utc(value)


class OrderIntent(BaseModel, frozen=True):
    symbol: str
    asset_class: Literal["CRYPTO_SPOT", "EQUITY", "FUTURES"]
    side: Literal["BUY", "SELL"]
    quantity: Decimal
    ref_price: Decimal
    notional: Decimal
    reduce_only: bool
    strategy_id: str
    strategy_version: str
    capital_pct: Decimal

    @field_validator("capital_pct")
    @classmethod
    def _quantize_capital_pct(cls, value: Decimal) -> Decimal:
        return value.quantize(_PCT_QUANTUM)


class EquityInputs(_AsOfModel, frozen=True):
    total_equity: Decimal | None = None
    available_balance: Decimal | None = None
    day_start_equity: Decimal | None = None
    peak_equity: Decimal | None = None
    daily_pnl_pct: Decimal | None = None
    drawdown_pct: Decimal | None = None
    account_daily_pnl_pct: Decimal | None = None
    account_drawdown_pct: Decimal | None = None

    @field_validator(
        "daily_pnl_pct", "drawdown_pct", "account_daily_pnl_pct", "account_drawdown_pct"
    )
    @classmethod
    def _quantize(cls, value: Decimal | None) -> Decimal | None:
        return _quantize_pct(value)


class ExposureSnapshot(_AsOfModel, frozen=True):
    gross_notional: Mapping[str, Decimal] = {}
    net_notional: Mapping[str, Decimal] = {}
    open_positions_count: int = 0
    position_quantity: Decimal | None = None
    symbol_market_value: Decimal | None = None
    gross_leverage: Decimal | None = None


class StatsInputs(_AsOfModel, frozen=True):
    var_pct: Decimal | None = None
    es_pct: Decimal | None = None
    var_method: str | None = None
    lookback_bars: int | None = None
    bars_used: int | None = None
    correlated_exposure_pct: Decimal | None = None
    max_correlation: float | None = None
    missing_pairs: tuple[str, ...] = ()

    @field_validator("var_pct", "es_pct", "correlated_exposure_pct")
    @classmethod
    def _quantize(cls, value: Decimal | None) -> Decimal | None:
        return _quantize_pct(value)


class ActivityInputs(BaseModel, frozen=True):
    trades_last_1h: int | None = None
    trades_avg_per_hour_24h: Decimal | None = None


class SafetyInputs(BaseModel, frozen=True):
    circuit_breaker_level: str | None = None
    active_control_scopes: tuple[str, ...] | None = None
    fence_snapshot: Mapping[str, int] | None = None
    data_distrust_level: str | None = None
    distrust_sources_available: int | None = None
    connection_fresh: bool | None = None
    execution_paused_by_safety: bool | None = None
    rule_bundle_active: bool | None = None


class RiskInputs(_AsOfModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    tenant_id: UUID
    execution_ref: str | None
    certified_badge: bool | None
    allocated_capital: Decimal | None
    intent: OrderIntent
    equity: EquityInputs
    exposure: ExposureSnapshot
    stats: StatsInputs
    activity: ActivityInputs
    safety: SafetyInputs
    limits: tuple[ExposureLimit, ...] = ()

    def inputs_hash(self) -> str:
        """`mode="python"`을 쓴다 — `mode="json"`은 `Decimal`을 미리 문자열로
        굳혀 `hashing.canonical_json`의 정규화를 무력화한다. R2(결정론)는
        같은 논리적 값이면 구성 경로가 달라도 같은 해시를 요구한다."""
        return sha256_hex(canonical_json(self.model_dump(mode="python")))

    @classmethod
    def from_legacy_dict(
        cls,
        allocation: Any,
        account_state: dict[str, Any],
        *,
        tenant_id: UUID,
        execution_id: int,
        now: datetime,
    ) -> RiskInputs:
        """레거시 `RiskEngine.check(allocation, account_state)` 호출부 어댑터
        — 조립 로직은 `legacy_bridge.py`(순환 import 회피, 180줄 상한)."""
        from src.core.risk.legacy_bridge import build_risk_inputs

        return build_risk_inputs(
            cls,
            allocation,
            account_state,
            tenant_id=tenant_id,
            execution_id=execution_id,
            now=now,
        )
