"""GetDrawings query — `create_layout()` always creates an empty document alongside
the layout, so `get_drawing_set()` never returns None on the normal path here
(since `load_owned_layout()` already confirmed the layout exists)."""

from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import drawing_set_to_view, load_owned_layout
from src.foundation.charting.contracts.v1 import DrawingsDocumentView
from src.foundation.charting.ports.repository import ChartingRepository


class DrawingSetMissingError(Exception):
    """Should not occur on the normal path (see module docstring) — if raised,
    the `create_layout()` invariant is broken, so surface it as an exception
    rather than silently swallowing it into an empty document."""


async def get_drawings(
    repo: ChartingRepository, *, tenant_id: UUID, layout_id: UUID
) -> DrawingsDocumentView:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    drawing_set = await repo.get_drawing_set(layout_id)
    if drawing_set is None:
        raise DrawingSetMissingError(str(layout_id))
    return drawing_set_to_view(drawing_set)
