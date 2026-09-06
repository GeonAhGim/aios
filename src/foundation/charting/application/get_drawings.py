"""GetDrawings 쿼리 — `create_layout()`이 항상 빈 문서를 함께 만들어 두므로
`get_drawing_set()`이 None을 반환하는 경우는 이 리프의 정상 경로에 없다
(레이아웃 존재를 이미 `load_owned_layout()`으로 확인했으므로)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import drawing_set_to_view, load_owned_layout
from src.foundation.charting.contracts.v1 import DrawingsDocumentView
from src.foundation.charting.ports.repository import ChartingRepository


class DrawingSetMissingError(Exception):
    """정상 경로에서는 발생하지 않는다(위 docstring) — 발생하면 `create_layout()`
    불변조건이 깨진 것이므로 조용히 빈 문서로 흘려보내지 않고 예외로 드러낸다."""


async def get_drawings(
    repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID
) -> DrawingsDocumentView:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    drawing_set = await repo.get_drawing_set(layout_id)
    if drawing_set is None:
        raise DrawingSetMissingError(str(layout_id))
    return drawing_set_to_view(drawing_set)
