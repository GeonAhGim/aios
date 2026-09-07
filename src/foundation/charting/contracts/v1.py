"""Charting 계약 v1 — `charting-v1`.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5, 107_contract_versioning_and_compatibility_standard_v1.0.md.

`DrawingsDocumentView.drawings`는 CH-4 `serialize.ts`(8bd4077)의
`DrawingsDocument.drawings`와 1:1 원소 형태(각 항목의 `id`/`kind`/kind별
필드/`locked`/`style`)를 유지한다 — pydantic 판별 유니언으로 다시 모델링
하지 않고 `dict`로 그대로 실어보낸다(형태 자체의 진위 검증은
`domain/rules.py`가 fail-closed로 담당하므로 이중 스키마를 두지 않는다).

MAJOR 변경 시 이 파일을 고치지 않고 `contracts/v2.py`를 새로 만든다(107번
§3.3).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

SCHEMA_VERSION = "v1"


class ChartLayoutView(BaseModel):
    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    name: str
    layout_state: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime
    schema_version: str = SCHEMA_VERSION


class CreateChartLayoutRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    layout_state: dict[str, Any] = Field(default_factory=dict)


class ChartIndicatorTemplateView(BaseModel):
    """`template` is CH-17a `templateModel.ts`'s `Template`
    (`encodeTemplate()` output) as-is — this contract does not remodel its
    internals either (same principle as `layout_state`, see the file's
    top-level docstring)."""

    id: UUID
    tenant_id: UUID
    owner_subject_id: UUID
    name: str
    template: dict[str, Any]
    revision: int
    created_at: datetime
    updated_at: datetime
    schema_version: str = SCHEMA_VERSION


class CreateChartIndicatorTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    template: dict[str, Any]


class UpdateChartLayoutRequest(BaseModel):
    """부분 갱신 — `name`/`layout_state` 중 최소 하나는 있어야 한다."""

    expected_revision: int
    name: str | None = Field(default=None, min_length=1, max_length=120)
    layout_state: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _require_at_least_one_field(self) -> UpdateChartLayoutRequest:
        if self.name is None and self.layout_state is None:
            raise ValueError("name 또는 layout_state 중 최소 하나는 있어야 합니다.")
        return self


class DrawingsDocumentView(BaseModel):
    """CH-4 `DrawingsDocument`와 1:1 — `schema_version`(int, 드로잉 문서 자체의
    버전, 이 계약 파일의 `SCHEMA_VERSION`(str)과 별개 개념) + `drawings`.
    `revision`은 낙관적 잠금용 부가 필드(CH-4에는 없음, MINOR 확장)."""

    layout_id: UUID
    schema_version: int
    drawings: list[dict[str, Any]]
    revision: int
    updated_at: datetime


class PutDrawingsRequest(BaseModel):
    expected_revision: int
    schema_version: int
    drawings: list[dict[str, Any]]
