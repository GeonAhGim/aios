"""Drawing document (pure) validation — Python mirror of CH-4 `serialize.ts`(8bd4077)
`fromDrawingsDocument`/`decodeDrawing`.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.2,
§9.6 CH-5 "Layout/drawings in 1:1 with CH-4 serialize format".

fail-closed — unknown version / missing fields / unknown fields / type mismatches all
raise `DrawingValidationError`. Never silently drop or coerce types like TS does.
Contains only pure functions with no I/O (adapter writes the result directly to jsonb)."""
from __future__ import annotations

from typing import Any, NoReturn

from src.foundation.charting.domain.models import DRAWING_KINDS, DRAWINGS_SCHEMA_VERSION

_COMMON_FIELDS = frozenset({"id", "kind", "locked", "style"})
_KIND_FIELDS: dict[str, frozenset[str]] = {
    "trendline": frozenset({"points"}),
    "rectangle": frozenset({"points"}),
    "fibonacci": frozenset({"points", "levels"}),
    "horizontal-line": frozenset({"price"}),
    "vertical-line": frozenset({"time"}),
}
_STYLE_FIELDS = frozenset({"color", "lineWidth"})
_DOC_FIELDS = frozenset({"schema_version", "drawings"})


class DrawingValidationError(Exception):
    """Surface all CH-4 `DrawingError` reasons (schema mismatch, missing fields,
    unknown fields, type mismatch, duplicate id) as a single exception — the router
    maps it to VALIDATION_INVALID_FIELD(400) (reusing existing code without a new
    taxonomy, task-1557 decision)."""


def _fail(slot: str, detail: str) -> NoReturn:
    raise DrawingValidationError(f"{slot}: {detail}")


def _require_object(slot: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(slot, "must be an object")
    return value


def _assert_known_fields(slot: str, obj: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = set(obj) - allowed
    if unknown:
        _fail(slot, f"unknown field(s) {sorted(unknown)} (refusing to drop)")


def _require_field(slot: str, obj: dict[str, Any], field: str) -> Any:
    if field not in obj:
        _fail(f"{slot}.{field}", "required")
    return obj[field]


def _finite_number(slot: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(slot, "must be a finite number")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):  # NaN/Inf
        _fail(slot, "must be a finite number")
    return number


def _validate_point(slot: str, value: Any) -> dict[str, float]:
    obj = _require_object(slot, value)
    _assert_known_fields(slot, obj, frozenset({"time", "price"}))
    return {
        "time": _finite_number(f"{slot}.time", _require_field(slot, obj, "time")),
        "price": _finite_number(f"{slot}.price", _require_field(slot, obj, "price")),
    }


def _validate_points(slot: str, value: Any) -> list[dict[str, float]]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(f"{slot}.points", "must be an array of exactly two points")
    return [
        _validate_point(f"{slot}.points[0]", value[0]),
        _validate_point(f"{slot}.points[1]", value[1]),
    ]


def _validate_levels(slot: str, value: Any) -> list[float]:
    if not isinstance(value, list) or len(value) == 0:
        _fail(f"{slot}.levels", "must be a non-empty array")
    return [_finite_number(f"{slot}.levels[{i}]", v) for i, v in enumerate(value)]


def _validate_style(slot: str, value: Any) -> dict[str, Any]:
    obj = _require_object(f"{slot}.style", value)
    _assert_known_fields(f"{slot}.style", obj, _STYLE_FIELDS)
    style: dict[str, Any] = {}
    if "color" in obj:
        color = obj["color"]
        if not isinstance(color, str) or len(color) == 0:
            _fail(f"{slot}.style.color", "must be a non-empty string")
        style["color"] = color
    if "lineWidth" in obj:
        width = _finite_number(f"{slot}.style.lineWidth", obj["lineWidth"])
        if width <= 0:
            _fail(f"{slot}.style.lineWidth", "must be > 0")
        style["lineWidth"] = width
    return style


def _validate_drawing(index: int, value: Any) -> dict[str, Any]:
    slot = f"drawings[{index}]"
    obj = _require_object(slot, value)
    raw_id = _require_field(slot, obj, "id")
    if not isinstance(raw_id, str) or len(raw_id) == 0:
        _fail(f"{slot}.id", "must be a non-empty string")
    drawing_id = raw_id

    kind = _require_field(drawing_id, obj, "kind")
    if kind not in DRAWING_KINDS:
        _fail(f"{drawing_id}.kind", f'unknown kind "{kind}"')
    _assert_known_fields(drawing_id, obj, _COMMON_FIELDS | _KIND_FIELDS[kind])

    out: dict[str, Any] = {"id": drawing_id, "kind": kind}
    if kind in ("trendline", "rectangle"):
        out["points"] = _validate_points(drawing_id, _require_field(drawing_id, obj, "points"))
    elif kind == "fibonacci":
        out["points"] = _validate_points(drawing_id, _require_field(drawing_id, obj, "points"))
        out["levels"] = _validate_levels(drawing_id, _require_field(drawing_id, obj, "levels"))
    elif kind == "horizontal-line":
        out["price"] = _finite_number(
            f"{drawing_id}.price", _require_field(drawing_id, obj, "price")
        )
    elif kind == "vertical-line":
        out["time"] = _finite_number(
            f"{drawing_id}.time", _require_field(drawing_id, obj, "time")
        )

    if "locked" in obj:
        if not isinstance(obj["locked"], bool):
            _fail(f"{drawing_id}.locked", "must be a boolean")
        out["locked"] = obj["locked"]
    if "style" in obj:
        out["style"] = _validate_style(drawing_id, obj["style"])
    return out


def validate_drawings_document(document: Any) -> tuple[int, tuple[dict[str, Any], ...]]:
    """Validate a raw `{schema_version, drawings}` dict (already JSON-parsed) and
    return `(schema_version, drawings)`. Fails in the same order as CH-4 `fromDrawingsDocument`:
    object check → schema_version presence/value → unknown fields → drawings array check →
    each drawing → duplicate id within document."""
    doc = _require_object("<document>", document)
    if "schema_version" not in doc:
        _fail("<document>.schema_version", "is missing")
    version = doc["schema_version"]
    if isinstance(version, bool) or version != DRAWINGS_SCHEMA_VERSION:
        _fail(
            "<document>.schema_version",
            f"{version!r} is not supported (expected {DRAWINGS_SCHEMA_VERSION})",
        )
    _assert_known_fields("<document>", doc, _DOC_FIELDS)
    raw_drawings = _require_field("<document>", doc, "drawings")
    if not isinstance(raw_drawings, list):
        _fail("<document>.drawings", "must be an array")

    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(raw_drawings):
        drawing = _validate_drawing(i, raw)
        if drawing["id"] in seen:
            _fail(drawing["id"], "duplicate id in document")
        seen.add(drawing["id"])
        validated.append(drawing)
    return DRAWINGS_SCHEMA_VERSION, tuple(validated)
