"""Charting 도메인 모델 — chart_layout·chart_drawing_set.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5. 드로잉 문서 형식은 `frontend/packages/chart-engine/src/drawings/
{model,tools,serialize}.ts`(CH-4, 8bd4077)의 `DrawingsDocument`와 1:1이다 —
`schema_version`/`drawings` 두 필드, `drawings[i]`의 `id`/`kind`/kind별
필드/`locked`/`style` 키가 그대로다. 이 파일은 순수 데이터클래스만 담고
저장(adapters/) I/O나 검증(rules.py) 로직을 갖지 않는다."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

# CH-4 model.ts DRAWING_KINDS와 1:1 — 여기서 새 종류를 추가하려면 두 파일을
# 함께 바꿔야 한다(직렬화 형식이 계약이므로 편측 변경은 왕복 파손).
DRAWING_KINDS: frozenset[str] = frozenset(
    {"trendline", "horizontal-line", "vertical-line", "rectangle", "fibonacci"}
)

# CH-4 serialize.ts DRAWINGS_SCHEMA_VERSION과 동일 값 — 다른 값은
# DrawingValidationError(CHART_DRAWING_SCHEMA_UNSUPPORTED 상당)로 거부한다.
DRAWINGS_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class ChartLayout:
    """멀티차트 레이아웃 1개 — `layout_state`는 CH-8 `layout-v1` 계약이 자리를
    잡을 때까지 이 리프에서는 불투명 JSON(symbol/timeframe/indicators/panes
    등 프론트 소유 구조)으로 취급한다. 이 파일이 그 내부 스키마를 검증하지
    않는다 — 검증 대상은 드로잉 문서(rules.py)뿐이다."""

    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    name: str
    layout_state: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ChartDrawingSet:
    """레이아웃 1개당 정확히 하나 — `chart_layout` 생성과 같은 트랜잭션으로
    빈 문서(revision=0)가 함께 생성된다(application/create_layout.py), 그래서
    이 저장소는 이 행이 없는 layout_id를 절대 취급하지 않는다(INSERT 분기가
    필요 없다 — put_drawings는 항상 조건부 UPDATE 하나뿐)."""

    layout_id: UUID
    schema_version: int
    drawings: tuple[dict[str, Any], ...]
    revision: int
    updated_at: datetime
