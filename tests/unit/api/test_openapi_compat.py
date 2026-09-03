"""PLT-16 — `check_openapi_compat.find_violations`(순수 JSON 비교) + CLI.

DB·네트워크·앱 임포트 전부 없음(decision) — 베이스라인/현재 스키마는 이
파일에서 직접 만든 dict/fixture 파일만 쓴다.
"""

from __future__ import annotations

import json

from scripts.check_openapi_compat import find_violations, main


def _schema(*, paths: dict, schemas: dict | None = None) -> dict:
    return {"paths": paths, "components": {"schemas": schemas or {}}}


def _get_ok_path() -> dict:
    return {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}
                    }
                }
            }
        }
    }


def _widget_schema(**overrides) -> dict:
    base = {
        "type": "object",
        "properties": {
            "id": {"type": "string", "format": "uuid"},
            "status": {"type": "string", "enum": ["ACTIVE", "PAUSED"]},
        },
    }
    base.update(overrides)
    return base


def test_identical_schema_has_no_violations():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    current = json.loads(json.dumps(baseline))  # 깊은 복사

    assert find_violations(baseline, current) == []


def test_removed_path_fails():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    current = _schema(paths={}, schemas={"Widget": _widget_schema()})

    violations = find_violations(baseline, current)

    assert any("path 제거: /widgets" in v for v in violations)


def test_removed_method_fails():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    current = _schema(paths={"/widgets": {}}, schemas={"Widget": _widget_schema()})

    violations = find_violations(baseline, current)

    assert any("method 제거: GET /widgets" in v for v in violations)


def test_removed_response_property_fails_with_field_name():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    status_only = {"status": _widget_schema()["properties"]["status"]}
    current = _schema(
        paths={"/widgets": _get_ok_path()},
        schemas={"Widget": _widget_schema(properties=status_only)},
    )

    violations = find_violations(baseline, current)

    assert any("response property 제거" in v and ".id" in v for v in violations)


def test_response_property_type_change_fails():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    changed = _widget_schema()
    changed["properties"]["id"] = {"type": "integer"}
    current = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": changed})

    violations = find_violations(baseline, current)

    assert any("response property type 변경" in v and ".id" in v for v in violations)


def test_response_enum_value_removed_fails():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    changed = _widget_schema()
    changed["properties"]["status"] = {"type": "string", "enum": ["ACTIVE"]}
    current = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": changed})

    violations = find_violations(baseline, current)

    assert any("response enum 값 제거" in v and "PAUSED" in v for v in violations)


def test_response_property_added_is_minor_and_passes():
    baseline = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    added = _widget_schema()
    added["properties"]["created_at"] = {"type": "string", "format": "date-time"}
    current = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": added})

    assert find_violations(baseline, current) == []


def _post_path() -> dict:
    return {
        "post": {
            "requestBody": {
                "content": {
                    "application/json": {"schema": {"$ref": "#/components/schemas/CreateWidget"}}
                }
            },
            "responses": {
                "201": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Widget"}}
                    }
                }
            },
        }
    }


def _create_widget_schema(**overrides) -> dict:
    base = {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    }
    base.update(overrides)
    return base


def test_added_required_request_field_fails():
    baseline = _schema(
        paths={"/widgets": _post_path()},
        schemas={"CreateWidget": _create_widget_schema(), "Widget": _widget_schema()},
    )
    changed = _create_widget_schema(
        properties={"name": {"type": "string"}, "owner": {"type": "string"}},
        required=["name", "owner"],
    )
    current = _schema(
        paths={"/widgets": _post_path()},
        schemas={"CreateWidget": changed, "Widget": _widget_schema()},
    )

    violations = find_violations(baseline, current)

    assert any("request required 추가" in v and "owner" in v for v in violations)


def test_optional_request_field_added_is_minor_and_passes():
    baseline = _schema(
        paths={"/widgets": _post_path()},
        schemas={"CreateWidget": _create_widget_schema(), "Widget": _widget_schema()},
    )
    changed = _create_widget_schema(
        properties={"name": {"type": "string"}, "note": {"type": "string"}}
    )
    current = _schema(
        paths={"/widgets": _post_path()},
        schemas={"CreateWidget": changed, "Widget": _widget_schema()},
    )

    assert find_violations(baseline, current) == []


def test_cli_exit_zero_on_identical_snapshots(tmp_path, capsys):
    schema = _schema(paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()})
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(schema), encoding="utf-8")
    current_path.write_text(json.dumps(schema), encoding="utf-8")

    exit_code = main(["--baseline", str(baseline_path), "--current", str(current_path)])

    assert exit_code == 0
    assert "OK:" in capsys.readouterr().out


def test_cli_exit_one_and_prints_removed_field_name(tmp_path, capsys):
    baseline_schema = _schema(
        paths={"/widgets": _get_ok_path()}, schemas={"Widget": _widget_schema()}
    )
    current_schema = _schema(
        paths={"/widgets": _get_ok_path()},
        schemas={
            "Widget": _widget_schema(
                properties={"status": _widget_schema()["properties"]["status"]}
            )
        },
    )
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(baseline_schema), encoding="utf-8")
    current_path.write_text(json.dumps(current_schema), encoding="utf-8")

    exit_code = main(["--baseline", str(baseline_path), "--current", str(current_path)])

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL:" in out
    assert ".id" in out  # 삭제된 필드명이 출력에 포함됨


def test_cli_fails_when_baseline_missing(tmp_path, capsys):
    missing = tmp_path / "does-not-exist.json"

    exit_code = main(["--baseline", str(missing)])

    assert exit_code == 1
    assert "FAIL:" in capsys.readouterr().out
