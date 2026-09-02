"""L4_risk_and_safety_v1.0.md#3.2, #2.1 — 노출 한도 값 객체(`RiskInputs.limits`).

`check_exposure_limits(inputs, limits) -> RuleResult`(R-14)는 이 값 객체와
`inputs.py`의 `RiskInputs`에 동시에 의존한다 — 순환을 피하기 위해 값
객체는 여기 두고 `inputs.py`가 이 모듈에서 import한다.
"""
from __future__ import annotations

from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class LimitScope(str, Enum):
    TENANT = "TENANT"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    SYMBOL = "SYMBOL"
    ASSET_CLASS = "ASSET_CLASS"
    PROVIDER = "PROVIDER"


class LimitMetric(str, Enum):
    GROSS_NOTIONAL_PCT = "GROSS_NOTIONAL_PCT"
    NET_NOTIONAL_PCT = "NET_NOTIONAL_PCT"
    MAX_ORDER_NOTIONAL = "MAX_ORDER_NOTIONAL"
    MAX_OPEN_POSITIONS = "MAX_OPEN_POSITIONS"
    MAX_TRADES_PER_HOUR = "MAX_TRADES_PER_HOUR"
    MAX_LEVERAGE = "MAX_LEVERAGE"


class ExposureLimit(BaseModel, frozen=True):
    scope: LimitScope
    scope_ref: str
    metric: LimitMetric
    limit_value: Decimal
    hard: bool
    limit_id: UUID
