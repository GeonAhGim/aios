"""ChartingRepository port. domain은 이 Protocol만 알고, 실제 구현(adapters/)은
모른다(71번 §4)."""
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
        """`chart_layout`과 빈(`revision=0`) `chart_drawing_set`을 한
        트랜잭션으로 함께 만든다 — 레이아웃이 존재하는데 드로잉 문서가 아직
        없는 상태를 만들지 않아, `put_drawings()`가 조건부 UPDATE 하나만으로
        충분하다(INSERT 분기·그로 인한 first-write 경합을 원천 제거)."""
        ...

    async def get_layout(self, layout_id: UUID) -> ChartLayout | None: ...

    async def list_layouts(self, tenant_id: UUID) -> tuple[ChartLayout, ...]: ...

    async def update_layout(
        self,
        layout_id: UUID,
        *,
        expected_revision: int,
        name: str | None,
        layout_state: dict[str, Any] | None,
    ) -> ChartLayout:
        """105번 표준 조건부 UPDATE — `expected_revision` 불일치는
        `ConcurrencyConflictError`(409)."""
        ...

    async def delete_layout(self, layout_id: UUID) -> None:
        """`chart_drawing_set`은 FK `ON DELETE CASCADE`로 함께 지워진다."""
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
        """105번 표준 조건부 UPDATE — `expected_revision` 불일치는
        `ConcurrencyConflictError`(409). `create_layout()`이 항상 먼저
        빈 문서를 만들어 두므로 이 메서드는 INSERT를 하지 않는다."""
        ...

    async def create_indicator_template(
        self,
        *,
        tenant_id: UUID,
        owner_subject_id: UUID,
        name: str,
        template: dict[str, Any],
    ) -> ChartIndicatorTemplate:
        """`(tenant_id, name)` UNIQUE 위반은 `ConcurrencyConflictError`(409,
        CH-5 chart_layout 경로와 동일하게 새 taxonomy 없이 재사용)."""
        ...

    async def get_indicator_template(self, template_id: UUID) -> ChartIndicatorTemplate | None: ...

    async def list_indicator_templates(
        self, tenant_id: UUID
    ) -> tuple[ChartIndicatorTemplate, ...]: ...

    async def delete_indicator_template(self, template_id: UUID) -> None: ...
