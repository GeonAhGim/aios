"""PLT-16 — `check_openapi_compat.find_violations`(순수 JSON 비교) + CLI.

DB·네트워크·앱 임포트 전부 없음(decision) — 베이스라인/현재 스키마는 이
파일에서 직접 만든 dict/fixture 파일만 쓴다.
"""

from __future__ import annotations

import json

import pytest

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


# --- PLT-16: anyOf(Optional) 정규화 ---------------------------------------
# Optional[X] -> OpenAPI 3.1 {"anyOf": [X, {"type": "null"}]}. 정규화 없이는
# anyOf 안의 변경을 못 잡는다(task-1213 reviewer 재현: password str->int가 exit 0).


def _optional(inner: dict) -> dict:
    return {"anyOf": [dict(inner), {"type": "null"}]}


def _login_bundle(password: dict) -> dict:
    ref = {"schema": {"$ref": "#/components/schemas/Login"}}
    path = {
        "post": {
            "requestBody": {"content": {"application/json": ref}},
            "responses": {"200": {"content": {"application/json": ref}}},
        }
    }
    login_schema = {"type": "object", "properties": {"password": password}}
    return _schema(paths={"/login": path}, schemas={"Login": login_schema})


def _ref_bundle(inner: dict) -> dict:
    bundle = _login_bundle({"$ref": "#/components/schemas/PasswordField"})
    bundle["components"]["schemas"]["PasswordField"] = _optional(inner)
    return bundle


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NULL = {"type": "null"}
_TYPE_CHANGE = "property type 변경"
_ARR_STR = {"type": "array", "items": _optional(_STR)}
_ARR_INT = {"type": "array", "items": _optional(_INT)}

_CASES = [
    # (baseline, current, MAJOR 위반이면 부분 문자열 / PASS면 None, case id)
    (_login_bundle(_optional(_STR)), _login_bundle(_optional(_INT)), _TYPE_CHANGE, "1-type-change"),
    (_login_bundle(_optional(_STR)), _login_bundle(_STR), "nullable 축소", "2-nullable-narrow"),
    (_login_bundle(_STR), _login_bundle(_optional(_STR)), None, "3-nullable-widen"),
    (_login_bundle({"anyOf": [_NULL, _STR]}), _login_bundle(_optional(_STR)), None, "4-order"),
    (_ref_bundle(_STR), _ref_bundle(_INT), _TYPE_CHANGE, "5a-ref-anyof"),
    (_login_bundle(_ARR_STR), _login_bundle(_ARR_INT), _TYPE_CHANGE, "5b-array-item"),
]


@pytest.mark.parametrize("baseline,current,expect_substr,_id", _CASES, ids=[c[3] for c in _CASES])
def test_optional_normalization_cases(baseline, current, expect_substr, _id):
    violations = find_violations(baseline, current)
    if expect_substr is None:
        assert violations == []
    else:
        assert any(expect_substr in v and ".password" in v for v in violations)


def test_reviewer_password_str_to_int_regression_now_fails(tmp_path, capsys):
    """task-1213 reviewer 재현 회귀: 수정 후 exit!=0으로 뒤집힘을 CLI 레벨로 확인."""
    baseline_path = tmp_path / "baseline.json"
    current_path = tmp_path / "current.json"
    baseline_path.write_text(json.dumps(_login_bundle(_optional(_STR))), encoding="utf-8")
    current_path.write_text(json.dumps(_login_bundle(_optional(_INT))), encoding="utf-8")
    exit_code = main(["--baseline", str(baseline_path), "--current", str(current_path)])
    out = capsys.readouterr().out
    assert exit_code != 0 and "password" in out


def test_identical_v1_snapshot_has_zero_violations():
    """수정 후 현행 contracts/openapi/v1.json을 자기 자신과 비교하면 exit 0(오탐 0)."""
    from pathlib import Path

    baseline_path = Path(__file__).resolve().parents[3] / "contracts" / "openapi" / "v1.json"
    if not baseline_path.exists():
        return  # 스냅샷이 없는 환경에서는 스킵
    snapshot = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert find_violations(snapshot, snapshot) == []
