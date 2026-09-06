"""PutDrawings 커맨드 — 드로잉 컬렉션 전체 치환 + 낙관적 잠금(105번 표준).

구조 검증(`domain.rules.validate_drawings_document`)을 소유권/낙관적 잠금
검사보다 먼저 한다 — 타 테넌트 layout_id에 망가진 문서를 보내도 400
(VALIDATION_INVALID_FIELD)이 먼저 나가버리면 "그 id가 존재한다"는 정보가
새므로, 실제로는 검증 자체는 layout_id와 무관한 순수 함수라 순서를 바꿔도
그 결과 자체는 같다 — 그래도 명시적으로 `load_owned_layout()`을 먼저
부르는 순서를 지킨다(get_drawings.py·update_layout.py와 동일 원칙 유지)."""
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
