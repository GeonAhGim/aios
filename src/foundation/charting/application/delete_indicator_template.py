"""DeleteChartIndicatorTemplate 커맨드 — 삭제 전 소유권 확인(미존재/타
테넌트 둘 다 404, delete_layout.py와 동일 원칙)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import load_owned_indicator_template
from src.foundation.charting.ports.repository import ChartingRepository


async def delete_indicator_template(
    repo: ChartingRepository, *, tenant_id: UUID, template_id: UUID
) -> None:
    await load_owned_indicator_template(repo, tenant_id=tenant_id, template_id=template_id)
    await repo.delete_indicator_template(template_id)
