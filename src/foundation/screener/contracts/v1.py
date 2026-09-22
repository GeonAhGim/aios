"""Screener contracts v1.

Spec: docs/specs/L4_product_experience_and_discovery_v1.0.md §2.2/§3
(`ScreenDefinition{filters, universe, sort, columns}`,
`Filter = IndicatorFilter|FundamentalFilter|ResearchFilter|BacktestStatFilter`),
ADR-2026-09-09-B Decision C(U-1).

Other bounded contexts consume only this file and never reference
domain/*.py directly (same convention as mandates/contracts/v1.py, 71 §4).

`Filter.condition` reuses the AIOS Script (src/core/script) boolean-expression
subset instead of inventing a new DSL (task-2628 decision) — actual
parsing, type checking (bool enforcement), and future-reference rejection
happen in domain/query_plan.py. This module only validates the string shape
(non-empty, length cap).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = "v1"

MAX_SAVED_SCREENERS_PER_TENANT = 50
MAX_CONDITION_SOURCE_LENGTH = 2000


def _validate_tz_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("must be tz-aware (naive datetime rejected)")
    return value


def _validate_condition_source(value: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("condition must not be empty")
    if len(stripped) > MAX_CONDITION_SOURCE_LENGTH:
        raise ValueError(f"condition exceeds max length ({MAX_CONDITION_SOURCE_LENGTH})")
    return stripped


class IndicatorFilter(BaseModel, frozen=True):
    kind: Literal["indicator"] = "indicator"
    condition: str

    @field_validator("condition")
    @classmethod
    def _check_condition(cls, value: str) -> str:
        return _validate_condition_source(value)


class FundamentalFilter(BaseModel, frozen=True):
    kind: Literal["fundamental"] = "fundamental"
    condition: str

    @field_validator("condition")
    @classmethod
    def _check_condition(cls, value: str) -> str:
        return _validate_condition_source(value)


class ResearchFilter(BaseModel, frozen=True):
    """Inherits the RD-A1 leak guard: an instance cannot be constructed at
    all without `as_of` (§3 "a ResearchFilter cannot run without as_of")."""

    kind: Literal["research"] = "research"
    condition: str
    as_of: datetime

    @field_validator("condition")
    @classmethod
    def _check_condition(cls, value: str) -> str:
        return _validate_condition_source(value)

    @field_validator("as_of")
    @classmethod
    def _check_as_of(cls, value: datetime) -> datetime:
        return _validate_tz_aware(value)


class BacktestStatFilter(BaseModel, frozen=True):
    kind: Literal["backtest_stat"] = "backtest_stat"
    condition: str

    @field_validator("condition")
    @classmethod
    def _check_condition(cls, value: str) -> str:
        return _validate_condition_source(value)


Filter = Annotated[
    IndicatorFilter | FundamentalFilter | ResearchFilter | BacktestStatFilter,
    Field(discriminator="kind"),
]


class SortSpec(BaseModel, frozen=True):
    field: str
    direction: Literal["asc", "desc"]


class ScreenDefinition(BaseModel, frozen=True):
    universe: str
    filters: tuple[Filter, ...]
    sort: SortSpec | None = None
    columns: tuple[str, ...] = ()
    schema_version: str = SCHEMA_VERSION

    @field_validator("universe")
    @classmethod
    def _check_universe(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("universe must not be empty")
        return value

    @field_validator("filters")
    @classmethod
    def _check_filters(cls, value: tuple[Filter, ...]) -> tuple[Filter, ...]:
        if len(value) == 0:
            raise ValueError("at least one filter is required")
        return value


class SavedScreenerView(BaseModel, frozen=True):
    id: UUID
    tenant_id: UUID
    name: str
    definition: ScreenDefinition
    created_at: datetime
    updated_at: datetime
    schema_version: str = SCHEMA_VERSION


# ---- UX-7: share (MP-3 immutable-version rule, applied locally) ----


class SharedScreenerView(BaseModel, frozen=True):
    """One immutable version of a shared screener (`shared_screeners`).

    `marketplace/domain/versioning.py` (MP-3) is still `hold`
    (L4_analytics_authoring_backtest_marketplace_v1.0.md §9.7), so
    `application/share_screen.py` applies its immutable-version
    invariant directly here instead of importing that module: each share
    INSERTs the next `version` for `screener_id`, existing rows are never
    UPDATEd."""

    id: UUID
    screener_id: UUID
    tenant_id: UUID
    name: str
    definition: ScreenDefinition
    version: int
    created_at: datetime
    schema_version: str = SCHEMA_VERSION


# ---- UX-7: alert_on_screen (conditional alert on a saved screen's match count) ----

MAX_ACTIVE_SCREEN_ALERTS_PER_TENANT = 50

ScreenAlertOperator = Literal["gte", "gt", "lte", "lt", "eq"]
ScreenAlertStatus = Literal["ACTIVE", "TRIGGERED", "CANCELLED"]


class ScreenAlertView(BaseModel, frozen=True):
    """Fires when a saved screen's matched-row count (`ScreenRunPage.total`,
    `application/run_screen.py`) satisfies `operator threshold` — mirrors
    `price_alerts`(FD-14, `src/services/alert_service.py`)'s
    threshold/status shape, but the compared quantity is a match count
    instead of a single indicator value."""

    id: UUID
    tenant_id: UUID
    screener_id: UUID
    operator: ScreenAlertOperator
    threshold: int
    status: ScreenAlertStatus
    created_at: datetime
    triggered_at: datetime | None = None
    triggered_count: int | None = None
    schema_version: str = SCHEMA_VERSION
