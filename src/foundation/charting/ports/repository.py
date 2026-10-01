"""ChartingRepository port. The domain knows only this Protocol; actual
implementations (adapters/) remain opaque (71 §4)."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from src.foundation.charting.domain.models import (
    ChartDrawingSet,
    ChartIndicatorTemplate,
    ChartLayout,
)


class ChartingRepository(Protocol):
    async def create_layout(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        layout_state: dict[str, Any],
    ) -> ChartLayout:
        """Create `chart_layout` and an empty (`revision=0`)
        `chart_drawing_set` in a single transaction — so that a layout
        existing without a drawing document is never left in an
        inconsistent state, and `put_drawings()` needs only a conditional
        UPDATE (eliminating the INSERT branch and its first-write contention)."""
        ...

    async def get_layout(self, layout_id: UUID) -> ChartLayout | None: ...

    async def list_layouts(self, tenant_id: UUID) -> tuple[ChartLayout, ...]: ...

    async def update_layout(
        self,
        layout_id: UUID,
        *,
        tenant_id: UUID,
        expected_revision: int,
        name: str | None,
        layout_state: dict[str, Any] | None,
    ) -> ChartLayout:
        """Standard-105 conditional UPDATE — a mismatched
        `expected_revision` raises `ConcurrencyConflictError` (409).
        `tenant_id` is also carried in the WHERE clause, defending again at
        this layer independent of the caller's ownership check."""
        ...

    async def delete_layout(self, layout_id: UUID, *, tenant_id: UUID) -> None:
        """`chart_drawing_set` is removed together by FK `ON DELETE CASCADE`.
        `tenant_id` is also carried in the WHERE clause, defending again at
        this layer."""
        ...

    async def get_drawing_set(self, layout_id: UUID) -> ChartDrawingSet | None: ...

    async def put_drawings(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        schema_version: int,
        drawings: tuple[dict[str, Any], ...],
    ) -> ChartDrawingSet:
        """Standard-105 conditional UPDATE — a mismatched
        `expected_revision` raises `ConcurrencyConflictError` (409).
        `create_layout()` always creates the empty document first, so this
        method never issues an INSERT."""
        ...

    async def create_indicator_template(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        template: dict[str, Any],
    ) -> ChartIndicatorTemplate:
        """`(tenant_id, name)` UNIQUE violation raises
        `ConcurrencyConflictError` (409, reused without a new taxonomy,
        same as the CH-5 chart_layout path)."""
        ...

    async def get_indicator_template(self, template_id: UUID) -> ChartIndicatorTemplate | None: ...

    async def list_indicator_templates(
        self, tenant_id: UUID
    ) -> tuple[ChartIndicatorTemplate, ...]: ...

    async def delete_indicator_template(self, template_id: UUID, *, tenant_id: UUID) -> None:
        """`tenant_id` is also carried in the WHERE clause, defending again at
        this layer independent of the caller's ownership check — same
        principle as `delete_layout()`."""
        ...
