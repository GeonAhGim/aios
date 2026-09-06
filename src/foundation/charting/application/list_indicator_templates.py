"""ListChartIndicatorTemplates 쿼리 — 호출자 tenant 소유 템플릿만(저장소
쿼리 자체가 tenant_id로 필터하므로 여기서 별도 소유권 검사가 필요 없다)."""
from __future__ import annotations

from uuid import UUID

from src.foundation.charting.application._shared import indicator_template_to_view
from src.foundation.charting.contracts.v1 import ChartIndicatorTemplateView
from src.foundation.charting.ports.repository import ChartingRepository


async def list_indicator_templates(
    repo: ChartingRepository, *, tenant_id: UUID
) -> list[ChartIndicatorTemplateView]:
    templates = await repo.list_indicator_templates(tenant_id)
    return [indicator_template_to_view(template) for template in templates]
