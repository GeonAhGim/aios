"""application 커맨드 공용 헬퍼 — 소유권 확인 + 뷰 변환.

`_load_owned_layout()`이 CH-5 DoD의 "타 테넌트도 404" 지점이다: 존재하지
않는 layout_id와 다른 테넌트 소유 layout_id를 정확히 같은 예외/문자열
형태로 구분 없이 취급해, 응답에서 "존재는 하는데 권한이 없다"는 정보가
새지 않는다(reconciliation `resolve_reconciliation.py`와 동일 패턴)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application.errors import (
    ChartLayoutNotFoundError,
    CrossTenantChartLayoutAccessError,
)
from src.foundation.charting.contracts.v1 import ChartLayoutView, DrawingsDocumentView
from src.foundation.charting.domain.models import ChartDrawingSet, ChartLayout
from src.foundation.charting.ports.repository import ChartingRepository


async def load_owned_layout(
    repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID
) -> ChartLayout:
    layout = await repo.get_layout(layout_id)
    if layout is None:
        raise ChartLayoutNotFoundError(str(layout_id))
    if layout.tenant_id != tenant_id:
        raise CrossTenantChartLayoutAccessError(str(layout_id))
    return layout


def layout_to_view(layout: ChartLayout) -> ChartLayoutView:
    return ChartLayoutView(
        id=layout.id,
        tenant_id=layout.tenant_id,
        owner_subject_id=layout.owner_subject_id,
        name=layout.name,
        layout_state=layout.layout_state,
        revision=layout.revision,
        created_at=layout.created_at,
        updated_at=layout.updated_at,
    )


def drawing_set_to_view(drawing_set: ChartDrawingSet) -> DrawingsDocumentView:
    return DrawingsDocumentView(
        layout_id=drawing_set.layout_id,
        schema_version=drawing_set.schema_version,
        drawings=list(drawing_set.drawings),
        revision=drawing_set.revision,
        updated_at=drawing_set.updated_at,
    )
