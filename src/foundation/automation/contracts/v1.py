"""U-4a rule schema v1 — condition(price/indicator/disclosure/time) -> action
(notify/order/hedge/kill).

Spec: docs/design/ADR-2026-09-09-B.md Decision C (U-4),
docs/specs/L4_product_experience_and_discovery_v1.0.md U-4 row.

Reuses `Operator`/`compare_value` from `src.services.condition_evaluation`
(already shared by AlertService/PreviewCalculator) instead of redefining
comparison semantics, and `OrderSide`/`OrderType` from `src.data.models.trading`
for the order action. Other bounded contexts should import only this module,
never `domain/*.py` directly (mirrors `foundation/screener/contracts/v1.py`).

Disclosure condition: no live filing feed is wired to this engine yet
(RD-20 OpenDART filings live in a different bounded context with no hook
here). `DisclosureCondition` only describes the schema; a caller must supply
pre-fetched `disclosures` in the evaluation snapshot. Building the adapter
that turns live OpenDART filings into per-bar `disclosures` sets is deferred
to the follow-up leaf that wires the API router.
"""

from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from src.data.models.trading import OrderSide, OrderType
from src.foundation.risk_gate.contracts.v1 import SafetyScope
from src.services.condition_evaluation import Operator

SCHEMA_VERSION = "v1"

__all__ = [
    "SCHEMA_VERSION",
    "Action",
    "ActionKind",
    "AutomationRule",
    "Condition",
    "ConditionKind",
    "DisclosureCondition",
    "HedgeAction",
    "IndicatorCondition",
    "InvalidRuleDefinitionError",
    "KillAction",
    "NotifyAction",
    "OrderAction",
    "PreviewResult",
    "PriceCondition",
    "PriceField",
    "RuleNotFoundError",
    "RuleStatus",
    "TimeCondition",
]


class InvalidRuleDefinitionError(ValueError):
    """Rule schema violation (zero conditions, misaligned per-symbol bars, ...) — save refused."""


class RuleNotFoundError(LookupError):
    """Rule not found or cross-tenant access — treat as 404, never leak existence."""


class ConditionKind(str, Enum):
    PRICE = "price"
    INDICATOR = "indicator"
    DISCLOSURE = "disclosure"
    TIME = "time"


class ActionKind(str, Enum):
    NOTIFY = "notify"
    ORDER = "order"
    HEDGE = "hedge"
    KILL = "kill"


class RuleStatus(str, Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    CANCELLED = "CANCELLED"


class PriceField(str, Enum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"


class PriceCondition(BaseModel, frozen=True):
    kind: Literal[ConditionKind.PRICE] = ConditionKind.PRICE
    symbol: str
    field: PriceField = PriceField.CLOSE
    operator: Operator
    threshold: Decimal


class IndicatorCondition(BaseModel, frozen=True):
    kind: Literal[ConditionKind.INDICATOR] = ConditionKind.INDICATOR
    symbol: str
    indicator: str
    params: dict[str, Decimal] = Field(default_factory=dict)
    operator: Operator
    threshold: Decimal


class DisclosureCondition(BaseModel, frozen=True):
    """Unverified: no live filing feed integration — only reads the
    `disclosures` set already populated in the evaluation snapshot (see module
    docstring above)."""

    kind: Literal[ConditionKind.DISCLOSURE] = ConditionKind.DISCLOSURE
    symbol: str
    filing_type: str


class TimeCondition(BaseModel, frozen=True):
    kind: Literal[ConditionKind.TIME] = ConditionKind.TIME
    at: time
    days_of_week: tuple[int, ...] | None = None
    """0=Monday ... 6=Sunday (matches `datetime.weekday()`). `None` means every day."""

    @field_validator("days_of_week")
    @classmethod
    def _validate_days(cls, value: tuple[int, ...] | None) -> tuple[int, ...] | None:
        if value is not None and any(d < 0 or d > 6 for d in value):
            raise ValueError("days_of_week only allows 0(Mon)..6(Sun)")
        return value


Condition = Annotated[
    PriceCondition | IndicatorCondition | DisclosureCondition | TimeCondition,
    Field(discriminator="kind"),
]


class NotifyAction(BaseModel, frozen=True):
    kind: Literal[ActionKind.NOTIFY] = ActionKind.NOTIFY
    message_template: str


class OrderAction(BaseModel, frozen=True):
    kind: Literal[ActionKind.ORDER] = ActionKind.ORDER
    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType = OrderType.MARKET
    limit_price: Decimal | None = None

    @field_validator("quantity")
    @classmethod
    def _validate_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("quantity는 0보다 커야 한다")
        return value


class HedgeAction(BaseModel, frozen=True):
    kind: Literal[ActionKind.HEDGE] = ActionKind.HEDGE
    symbol: str
    hedge_symbol: str
    quantity: Decimal

    @field_validator("quantity")
    @classmethod
    def _validate_quantity(cls, value: Decimal) -> Decimal:
        if value <= 0:
            raise ValueError("quantity는 0보다 커야 한다")
        return value


class KillAction(BaseModel, frozen=True):
    kind: Literal[ActionKind.KILL] = ActionKind.KILL
    scope: SafetyScope
    scope_ref: str | None = None
    reason: str


Action = Annotated[
    NotifyAction | OrderAction | HedgeAction | KillAction,
    Field(discriminator="kind"),
]

GATED_ACTION_KINDS = (ActionKind.ORDER, ActionKind.HEDGE, ActionKind.KILL)
"""Live-trading actions — must pass the gate (risk ∩ compliance) to execute (U-4 DoD)."""


class AutomationRule(BaseModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    rule_id: UUID
    tenant_id: UUID
    name: str
    conditions: tuple[Condition, ...]
    action: Action
    status: RuleStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("conditions")
    @classmethod
    def _validate_conditions(cls, value: tuple[Condition, ...]) -> tuple[Condition, ...]:
        if not value:
            raise InvalidRuleDefinitionError("규칙에는 조건이 최소 1개 필요하다")
        return value


class PreviewResult(BaseModel, frozen=True):
    schema_version: Literal["v1"] = "v1"
    trigger_count: int
    evaluated_bars: int
    first_trigger_at: datetime | None
    last_trigger_at: datetime | None
    data_fingerprint: str
    """`sha256(canonical_json(bars))`; identical on rerun with same data (determinism proof)."""
