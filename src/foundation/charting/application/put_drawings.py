"""PutDrawings command — full replacement of the drawings collection + optimistic lock (standard-105).

Run structural validation (`domain.rules.validate_drawings_document`) before
ownership/optimistic-lock checks — if we send a broken document to another
tenant's layout_id and get a 400 (VALIDATION_INVALID_FIELD) first, the
response leaks the information that "that id exists". In reality the
validation itself is a pure function independent of layout_id, so swapping
the order would produce the same result — but we still explicitly follow
the order of calling `load_owned_layout()` first (same principle as
get_drawings.py and update_layout.py)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from src.foundation.charting.application._shared import drawing_set_to_view, load_owned_layout
from src.foundation.charting.contracts.v1 import DrawingsDocumentView
from src.foundation.charting.domain.rules import validate_drawings_document
from src.foundation.charting.ports.repository import ChartingRepository


async def put_drawings(
    repo: ChartingRepository,
    *,
    tenant_id: UUID,
    layout_id: UUID,
    expected_revision: int,
    schema_version: int,
    drawings: list[dict[str, Any]],
) -> DrawingsDocumentView:
    await load_owned_layout(repo, tenant_id=tenant_id, layout_id=layout_id)
    validated_version, validated_drawings = validate_drawings_document(
        {"schema_version": schema_version, "drawings": drawings}
    )
    drawing_set = await repo.put_drawings(
        layout_id,
        expected_revision=expected_revision,
        schema_version=validated_version,
        drawings=validated_drawings,
    )
    return drawing_set_to_view(drawing_set)
