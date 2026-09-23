"""Charting domain models — chart_layout, chart_drawing_set.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5. Drawing document format is 1:1 with `DrawingsDocument` from
`frontend/packages/chart-engine/src/drawings/{model,tools,serialize}.ts`
(CH-4, 8bd4077) — two fields `schema_version`/`drawings`, where each
`drawings[i]` carries `id`/`kind`/kind-specific fields/`locked`/`style` keys
as-is. This file holds pure dataclasses only and contains no storage
(adapters/) I/O or validation (rules.py) logic."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# 1:1 with CH-4 model.ts DRAWING_KINDS — adding a new kind here requires
# changing both files together (serialization format is a contract, so
# unilateral changes break round-trip compatibility).
DRAWING_KINDS: frozenset[str] = frozenset(
    {"trendline", "horizontal-line", "vertical-line", "rectangle", "fibonacci"}
)

# Same value as CH-4 serialize.ts DRAWINGS_SCHEMA_VERSION — other values
# are rejected via DrawingValidationError (maps to CHART_DRAWING_SCHEMA_UNSUPPORTED).
DRAWINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ChartLayout:
    """One multi-chart layout — `layout_state` is treated as opaque JSON
    (front-end-owned structure: symbol/timeframe/indicators/panes, etc.)
    until the CH-8 `layout-v1` contract solidifies. This file does not
    validate its internal schema — the drawing document (rules.py) is the
    sole validation target."""

    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    name: str
    layout_state: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChartIndicatorTemplate:
    """Indicator template (CH-17) — `template` is opaque data carrying
    CH-17a `templateModel.ts`'s `Template` (schemaVersion/panes/indicators)
    JSON through as-is (same principle as `ChartLayout.layout_state` —
    neither this file nor `adapters/` validate its internal schema;
    validation is owned by the frontend's `decodeTemplate()` as a
    frontend-owned contract). Unique on `(tenant_id, name)` — re-saving
    under the same name is delete-then-recreate, not a new UPDATE API (this
    leaf's scope has no update)."""

    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    name: str
    template: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChartDrawingSet:
    """Exactly one per layout — created alongside the `chart_layout` in the
    same transaction as an empty document (revision=0) by
    application/create_layout.py, so this store never encounters a
    layout_id without this row (no INSERT branch needed — put_drawings
    always performs a single conditional UPDATE)."""

    layout_id: UUID
    schema_version: int
    drawings: tuple[dict[str, Any], ...]
    revision: int
    updated_at: datetime
