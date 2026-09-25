"""PLT-16 — `check_openapi_compat.find_violations`: anyOf(Optional) 정규화 +
중첩 `$ref`(봉투/배열 items) 프로퍼티 비교.

DB·네트워크·앱 임포트 전부 없음(decision) — 베이스라인/현재 스키마는 이
파일에서 직접 만든 dict/fixture만 쓴다.

task-3940: test_openapi_compat.py(772줄) 분할 2/3 — 정규화·중첩 $ref 관심사.
1/3은 test_openapi_compat_paths.py(path/method/CLI 기본), 3/3은
test_openapi_compat_actions.py(실패주입·성능·서브프로세스).
"""

from __future__ import annotations

import json

import pytest

from scripts.check_openapi_compat import find_violations, main


def _schema(*, paths: dict, schemas: dict | None = None) -> dict:
    return {"paths": paths, "components": {"schemas": schemas or {}}}


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


# --- PLT-16 후속: $ref 봉투/배열 items 안쪽 프로퍼티 변경 --------------------
# task-1305 QA 발견 실측 재현: AccountConnectionView.created_at/status를
# ApiResponse_AccountConnectionView_.data 경유($ref, anyOf 아님)로 삭제해도
# 수정 전에는 exit 0였다.


def _envelope_bundle(inner_props: dict) -> dict:
    """`ApiResponse_Inner_.data`가 평범한 `$ref`로 `Inner`를 가리키는 봉투."""
    path = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Envelope"}}
                    }
                }
            }
        }
    }
    return _schema(
        paths={"/items": path},
        schemas={
            "Envelope": {
                "type": "object",
                "properties": {"data": {"$ref": "#/components/schemas/Inner"}},
                "required": ["data"],
            },
            "Inner": {"type": "object", "properties": inner_props},
        },
    )


def test_ref_envelope_nested_property_removed_fails():
    baseline = _envelope_bundle({"id": _STR, "status": {"type": "string", "enum": ["A", "B"]}})
    current = _envelope_bundle({"id": _STR})

    violations = find_violations(baseline, current)

    assert any("response property 제거" in v and ".data .status" in v for v in violations)


def test_ref_envelope_nested_property_type_change_fails():
    baseline = _envelope_bundle({"id": _STR})
    current = _envelope_bundle({"id": _INT})

    violations = find_violations(baseline, current)

    assert any(_TYPE_CHANGE in v and ".data .id" in v for v in violations)


def test_ref_envelope_nested_enum_narrowed_fails():
    baseline = _envelope_bundle({"status": {"type": "string", "enum": ["A", "B"]}})
    current = _envelope_bundle({"status": {"type": "string", "enum": ["A"]}})

    violations = find_violations(baseline, current)

    assert any("enum 값 제거" in v and ".data .status" in v and "B" in v for v in violations)


def test_ref_envelope_nested_property_added_is_minor_and_passes():
    baseline = _envelope_bundle({"id": _STR})
    current = _envelope_bundle({"id": _STR, "note": _STR})

    assert find_violations(baseline, current) == []


def _array_ref_bundle(item_props: dict) -> dict:
    """응답 프로퍼티가 `$ref` 객체 배열(items가 `$ref`)인 경우."""
    path = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Page"}}
                    }
                }
            }
        }
    }
    return _schema(
        paths={"/items": path},
        schemas={
            "Page": {
                "type": "object",
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {"$ref": "#/components/schemas/Row"},
                    }
                },
            },
            "Row": {"type": "object", "properties": item_props},
        },
    )


def test_array_items_ref_nested_property_removed_fails():
    baseline = _array_ref_bundle({"id": _STR, "label": _STR})
    current = _array_ref_bundle({"id": _STR})

    violations = find_violations(baseline, current)

    assert any("response property 제거" in v and ".results[] .label" in v for v in violations)


def test_array_items_ref_nested_property_type_change_fails():
    baseline = _array_ref_bundle({"id": _STR})
    current = _array_ref_bundle({"id": _INT})

    violations = find_violations(baseline, current)

    assert any(_TYPE_CHANGE in v and ".results[] .id" in v for v in violations)


def test_self_referential_ref_cycle_terminates_without_error():
    """`$ref` 순환참조(Node.children -> Node)에서 무한재귀하지 않고 종료해야 한다."""
    path = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Node"}}
                    }
                }
            }
        }
    }
    node_schema = {
        "type": "object",
        "properties": {
            "id": _STR,
            "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
        },
    }
    baseline = _schema(paths={"/tree": path}, schemas={"Node": node_schema})
    current = json.loads(json.dumps(baseline))

    assert find_violations(baseline, current) == []
