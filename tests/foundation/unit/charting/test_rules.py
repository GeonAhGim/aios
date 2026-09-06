"""domain/rules.py 순수 검증 단위테스트 — CH-4 `serialize.ts`(8bd4077)
`fromDrawingsDocument`/`decodeDrawing`과 1:1 실패 사유를 확인한다. DB 없이
동작(도메인 계층은 I/O가 없다)."""
from __future__ import annotations

import pytest

from src.foundation.charting.domain.rules import (
    DrawingValidationError,
    validate_drawings_document,
)


def test_valid_document_round_trips_each_kind():
    doc = {
        "schema_version": 1,
        "drawings": [
            {
                "id": "t1",
                "kind": "trendline",
                "points": [{"time": 1, "price": 1}, {"time": 2, "price": 2}],
            },
            {"id": "h1", "kind": "horizontal-line", "price": 100.5},
            {"id": "v1", "kind": "vertical-line", "time": 42},
            {
                "id": "r1",
                "kind": "rectangle",
                "points": [{"time": 1, "price": 1}, {"time": 2, "price": 2}],
                "locked": True,
                "style": {"color": "#fff", "lineWidth": 2},
            },
            {
                "id": "f1",
                "kind": "fibonacci",
                "points": [{"time": 1, "price": 1}, {"time": 2, "price": 2}],
                "levels": [0, 0.5, 1],
            },
        ],
    }
    version, drawings = validate_drawings_document(doc)
    assert version == 1
    assert len(drawings) == 5
    assert drawings[3]["style"] == {"color": "#fff", "lineWidth": 2}


def test_missing_schema_version_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document({"drawings": []})


def test_unsupported_schema_version_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document({"schema_version": 2, "drawings": []})


def test_boolean_schema_version_not_coerced_to_matching_int():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document({"schema_version": True, "drawings": []})


def test_unknown_top_level_field_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document({"schema_version": 1, "drawings": [], "extra": True})


def test_drawings_not_a_list_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document({"schema_version": 1, "drawings": {}})


def test_missing_required_field_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {"schema_version": 1, "drawings": [{"id": "d1", "kind": "horizontal-line"}]}
        )


def test_unknown_kind_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {"schema_version": 1, "drawings": [{"id": "d1", "kind": "circle", "price": 1}]}
        )


def test_unknown_field_rejected_not_silently_dropped():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [{"id": "d1", "kind": "horizontal-line", "price": 1, "bogus": 1}],
            }
        )


def test_non_finite_number_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [{"id": "d1", "kind": "horizontal-line", "price": float("nan")}],
            }
        )


def test_wrong_type_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [{"id": "d1", "kind": "horizontal-line", "price": "100"}],
            }
        )


def test_points_must_be_exactly_two():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [
                    {"id": "d1", "kind": "trendline", "points": [{"time": 1, "price": 1}]}
                ],
            }
        )


def test_fibonacci_requires_non_empty_levels():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [
                    {
                        "id": "d1",
                        "kind": "fibonacci",
                        "points": [{"time": 1, "price": 1}, {"time": 2, "price": 2}],
                        "levels": [],
                    }
                ],
            }
        )


def test_style_line_width_must_be_positive():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [
                    {
                        "id": "d1",
                        "kind": "vertical-line",
                        "time": 1,
                        "style": {"lineWidth": 0},
                    }
                ],
            }
        )


def test_duplicate_id_in_document_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {
                "schema_version": 1,
                "drawings": [
                    {"id": "dup", "kind": "vertical-line", "time": 1},
                    {"id": "dup", "kind": "vertical-line", "time": 2},
                ],
            }
        )


def test_empty_id_rejected():
    with pytest.raises(DrawingValidationError):
        validate_drawings_document(
            {"schema_version": 1, "drawings": [{"id": "", "kind": "vertical-line", "time": 1}]}
        )
