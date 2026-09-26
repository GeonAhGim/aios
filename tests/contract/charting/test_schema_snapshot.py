"""Charting v1 계약 DTO 스냅샷 테스트 — CH-5.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.6
CH-5, 107_contract_versioning_and_compatibility_standard_v1.0.md §3. 필드
집합을 고정해 이후 누군가 필드를 몰래 지우거나 타입을 바꾸면(MAJOR) 이
테스트가 즉시 깨지게 한다(`model_fields` 서브셋 검사라 optional 필드
추가는 이 테스트를 건드리지 않는다)."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from src.foundation.charting.contracts.v1 import (
    SCHEMA_VERSION,
    ChartIndicatorTemplateView,
    ChartLayoutView,
    CreateChartIndicatorTemplateRequest,
    CreateChartLayoutRequest,
    DrawingsDocumentView,
    PutDrawingsRequest,
    UpdateChartLayoutRequest,
)

_REQUIRED_FIELDS: dict[type[BaseModel], set[str]] = {
    ChartLayoutView: {
        "id",
        "tenant_id",
        "owner_subject_id",
        "name",
        "layout_state",
        "revision",
        "created_at",
        "updated_at",
        "schema_version",
    },
    CreateChartLayoutRequest: {"name", "layout_state"},
    UpdateChartLayoutRequest: {"expected_revision", "name", "layout_state"},
    DrawingsDocumentView: {"layout_id", "schema_version", "drawings", "revision", "updated_at"},
    PutDrawingsRequest: {"expected_revision", "schema_version", "drawings"},
    ChartIndicatorTemplateView: {
        "id",
        "tenant_id",
        "owner_subject_id",
        "name",
        "template",
        "revision",
        "created_at",
        "updated_at",
        "schema_version",
    },
    CreateChartIndicatorTemplateRequest: {"name", "template"},
}


def test_dto_field_sets_are_stable() -> None:
    for model, expected_fields in _REQUIRED_FIELDS.items():
        actual_fields = set(model.model_fields)
        missing = expected_fields - actual_fields
        assert not missing, f"{model.__name__}에서 필드가 사라짐(MAJOR 변경?): {missing}"


def test_schema_version_is_v1() -> None:
    assert SCHEMA_VERSION == "v1"


def test_update_request_rejects_empty_patch() -> None:
    """이름도 layout_state도 없는 PATCH는 아무것도 바꾸지 않는 요청이라
    전송 규격 단계에서 거부한다(422 → VALIDATION_INVALID_FIELD)."""
    with pytest.raises(ValidationError):
        UpdateChartLayoutRequest(expected_revision=0)


def test_update_request_accepts_name_only() -> None:
    body = UpdateChartLayoutRequest(expected_revision=0, name="renamed")
    assert body.layout_state is None


def test_create_request_defaults_layout_state_to_empty_object() -> None:
    body = CreateChartLayoutRequest(name="x")
    assert body.layout_state == {}


def test_put_drawings_request_carries_ch4_document_shape() -> None:
    body = PutDrawingsRequest(
        expected_revision=0,
        schema_version=1,
        drawings=[{"id": "d1", "kind": "vertical-line", "time": 1}],
    )
    assert body.model_dump()["drawings"][0]["kind"] == "vertical-line"


def test_create_chart_layout_rejects_empty_name() -> None:
    """`name`은 min_length=1 — 빈 문자열은 전송 규격 단계에서 거부한다."""
    with pytest.raises(ValidationError):
        CreateChartLayoutRequest(name="")


def test_create_indicator_template_rejects_missing_template() -> None:
    """`template`은 필수 필드 — 누락하면 계약 위반으로 거부한다."""
    with pytest.raises(ValidationError):
        CreateChartIndicatorTemplateRequest.model_validate({"name": "tmpl"})


def test_update_request_rejects_name_over_max_length() -> None:
    """`name`은 max_length=120 — 초과 시 거부한다(불변식 위반 입력)."""
    with pytest.raises(ValidationError):
        UpdateChartLayoutRequest(expected_revision=0, name="x" * 121)


def test_update_request_validator_failure_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_require_at_least_one_field`(의존 검증기)가 예기치 않은 예외를 던지면
    삼켜지지 않고 그대로 전파돼야 한다 — fail-closed 기본 정책(CLAUDE.md §3)의
    실패주입 확인. pydantic-core는 `model_validator`를 클래스 정의 시점의
    `__pydantic_decorators__` 참조로 컴파일하므로, 클래스 속성만 바꾸는
    `monkeypatch.setattr`로는 재현되지 않는다 — decorator info의 `func`를
    직접 바꾸고 `model_rebuild(force=True)`로 재컴파일해야 한다."""

    def _boom(self: UpdateChartLayoutRequest) -> UpdateChartLayoutRequest:
        raise RuntimeError("dependency exploded")

    decorator = UpdateChartLayoutRequest.__pydantic_decorators__.model_validators[
        "_require_at_least_one_field"
    ]
    original_func = decorator.func
    monkeypatch.setattr(decorator, "func", _boom, raising=True)
    UpdateChartLayoutRequest.model_rebuild(force=True)
    try:
        with pytest.raises(RuntimeError, match="dependency exploded"):
            UpdateChartLayoutRequest(expected_revision=0, name="renamed")
    finally:
        monkeypatch.setattr(decorator, "func", original_func, raising=True)
        UpdateChartLayoutRequest.model_rebuild(force=True)
