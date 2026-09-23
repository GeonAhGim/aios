"""PLT-16 — `check_openapi_compat.find_violations` + CLI: 실패주입·수치성능단언·
게이트적색재현·서브프로세스(export_openapi) 경계 동작.

DB·네트워크·앱 임포트 전부 없음(decision) — 베이스라인/현재 스키마는 이
파일에서 직접 만든 dict/fixture만 쓴다.

task-3940: test_openapi_compat.py(772줄) 분할 3/3 — DEEPEN(task-1956/3151)
실패주입·성능·서브프로세스 관심사. 1/3은 test_openapi_compat_paths.py
(path/method/CLI 기본), 2/3은 test_openapi_compat_schemas.py(정규화·중첩 $ref).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, cast

import pytest

import scripts.check_openapi_compat as compat_module
from scripts.check_openapi_compat import find_violations, main


def _schema(*, paths: dict[str, Any], schemas: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"paths": paths, "components": {"schemas": schemas or {}}}


_TYPE_CHANGE = "property type 변경"


# ── DEEPEN 1956: 실패주입·수치성능단언·게이트적색재현 ──────────────────────


def _api_response_envelope(
    data_ref: str, meta_ref: str = "#/components/schemas/Meta"
) -> dict[str, Any]:
    """ApiResponse_XXX_ 봉투 스키마를 만든다."""
    return {
        "type": "object",
        "properties": {
            "data": {"$ref": data_ref},
            "meta": {"$ref": meta_ref},
        },
    }


def _meta_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "as_of": {"type": "string", "format": "date-time"},
            "trace_id": {"type": "string"},
        },
    }


def _string_prop(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "string", "format": "uuid"}
    base.update(overrides)
    return base


def _integer_prop(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"type": "integer"}
    base.update(overrides)
    return base


def _array_items_ref(data_ref: str) -> dict[str, Any]:
    """배열 items의 $ref 패턴."""
    return {"type": "array", "items": {"$ref": data_ref}}


def test_failure_injection_deleted_nested_property_via_envelope_fails() -> None:
    """실패주입: 봉투($ref) 경유로 중첩 프로퍼티 삭제 시 MAJOR 위반을 캐야 한다.

    실측 시나리오: AccountConnectionView.created_at를 봉투 ApiResponse_AccountConnectionView_.data
    경유로 삭제했을 때 exit 0이 되는 이전 버그가 재현되어야 한다.
    """
    data_schema = {
        "type": "object",
        "properties": {
            "connection_id": _string_prop(),
            "created_at": _string_prop(format="date-time"),
            "state": {"type": "string", "enum": ["PENDING", "ACTIVE", "REVOKED"]},
        },
    }
    baseline = _schema(
        paths={
            "/connections": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ApiResponse_Conns"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={
            "ApiResponse_Conns": _api_response_envelope("#/components/schemas/ConnList"),
            "ConnList": {
                "type": "object",
                "properties": {
                    "connections": _array_items_ref("#/components/schemas/Conn"),
                    "meta": _meta_schema(),
                },
            },
            "Conn": data_schema,
            "Meta": _meta_schema(),
        },
    )
    # 실패 주입: Conn.created_at 삭제
    current = json.loads(json.dumps(baseline))
    current["components"]["schemas"]["Conn"]["properties"].pop("created_at")

    violations = find_violations(baseline, current)

    assert any("response property 제거" in v and ".created_at" in v for v in violations), (
        f"created_at 삭제를 탐지해야 함: {violations}"
    )


def test_failure_injection_enum_reduction_via_nested_ref_fails() -> None:
    """실패주입: $ref로 참조된 스키마의 enum 축소를 탐지해야 한다.

    enum 항목이 줄어들면 MAJOR 위반이어야 한다.
    """
    data_schema = {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["ACTIVE", "PAUSED", "SUSPENDED"]},
        },
    }
    baseline = _schema(
        paths={
            "/items": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ApiResponse_Items"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={
            "ApiResponse_Items": _api_response_envelope("#/components/schemas/Item"),
            "Item": data_schema,
            "Meta": _meta_schema(),
        },
    )
    # 실패 주입: enum에서 "SUSPENDED" 제거
    current = json.loads(json.dumps(baseline))
    current["components"]["schemas"]["Item"]["properties"]["status"]["enum"] = ["ACTIVE", "PAUSED"]

    violations = find_violations(baseline, current)

    assert any("enum" in v and ".status" in v for v in violations), (
        f"enum 축소를 탐지해야 함: {violations}"
    )


def test_failure_injection_type_change_nested_via_array_items_fails() -> None:
    """실패주입: 배열 items의 $ref 대상에서 타입 변경을 탐지해야 한다.

    배열 items로 참조된 객체의 프로퍼티 타입이 변경되면 MAJOR 위반.
    """
    row_schema = {
        "type": "object",
        "properties": {
            "value": _string_prop(),
        },
    }
    baseline = _schema(
        paths={
            "/rows": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ApiResponse_Rows"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={
            "ApiResponse_Rows": _api_response_envelope("#/components/schemas/Rows"),
            "Rows": {
                "type": "object",
                "properties": {
                    "rows": _array_items_ref("#/components/schemas/Row"),
                    "meta": _meta_schema(),
                },
            },
            "Row": row_schema,
            "Meta": _meta_schema(),
        },
    )
    # 실패 주입: Row.value 타입을 string → integer로 변경
    current = json.loads(json.dumps(baseline))
    current["components"]["schemas"]["Row"]["properties"]["value"]["type"] = "integer"

    violations = find_violations(baseline, current)

    assert any(_TYPE_CHANGE in v and ".value" in v for v in violations), (
        f"타입 변경을 탐지해야 함: {violations}"
    )


def test_performance_assertion_nested_ref_recursion_budget() -> None:
    """수치 성능 단언: 중첩 $ref 재귀가 100개 스키마에서도 1초 이내에 완료된다.

    PLT-16의 재귀 비교가 스키마 수가 많아져도 실용적인 시간 내에 종료되어야 한다.
    """
    import time

    # 100개의 중첩 스키마 체인 생성
    schemas = {"Meta": _meta_schema()}
    paths = {}
    for i in range(100):
        ref_name = f"Level{i}"
        inner_ref = "#/components/schemas/Inner" if i == 0 else f"#/components/schemas/Level{i - 1}"
        schemas[ref_name] = {
            "type": "object",
            "properties": {
                "id": _string_prop(),
                "next": {"$ref": inner_ref},
            },
        }
    # 시작점
    paths["/chain"] = {
        "get": {
            "responses": {
                "200": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/Level0"}}
                    }
                }
            }
        }
    }
    baseline = _schema(paths=paths, schemas=schemas)
    current = json.loads(json.dumps(baseline))

    # 성능 측정
    start = time.perf_counter()
    violations = find_violations(baseline, current)
    elapsed = time.perf_counter() - start

    # 성능 단언: 100개 스키마 체인에서 1초 이내
    assert elapsed < 1.0, f"100개 스키마 재귀가 {elapsed:.3f}초로 1초 한도를 초과"
    # 동일 스키마이므로 위반이 없어야 함
    assert violations == [], f"동일 스키마에서 위반이 없어야 함: {violations}"


def test_gate_red_reproduction_account_connection_view_deletion() -> None:
    """게이트적색재현: 이전 실측 시나리오(AccountConnectionView.created_at 삭제) 재현.

     실 v1.json 패턴: ApiResponse_AccountConnectionView_.data → AccountConnectionView
    에서 created_at을 삭제하면 게이트가 빨개져야 한다(exit 1).
    """
    acct_view = {
        "type": "object",
        "properties": {
            "connection_id": _string_prop(),
            "created_at": _string_prop(format="date-time"),
            "type": {"type": "string", "enum": ["BITGET", "KIS"]},
        },
    }
    baseline = _schema(
        paths={
            "/foundation/connections": {
                "post": {
                    "responses": {
                        "201": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "$ref": (
                                            "#/components/schemas/"
                                            "ApiResponse_AccountConnectionView_"
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={
            "ApiResponse_AccountConnectionView_": _api_response_envelope(
                "#/components/schemas/AccountConnectionView"
            ),
            "AccountConnectionView": acct_view,
            "Meta": _meta_schema(),
        },
    )
    # 게이트 적색 재현: created_at 삭제
    current = json.loads(json.dumps(baseline))
    current["components"]["schemas"]["AccountConnectionView"]["properties"].pop("created_at")

    violations = find_violations(baseline, current)

    # 게이트가 빨개져야 함: MAJOR 위반이 하나 이상 있어야 함
    assert len(violations) >= 1, "created_at 삭제 시 MAJOR 위반이 발생해야 함"
    assert any("response property 제거" in v and ".created_at" in v for v in violations), (
        f"created_at 삭제를 정확히 탐지해야 함: {violations}"
    )


# --- PLT-16 DEEPEN(task-3151): 실패 주입 -----------------------------------
# `--current` 생략 시 `main()`은 `export_openapi.py`를 서브프로세스로 실행해
# "현재" 스키마를 얻는다(export_openapi_current 경유). 이 외부 의존이 깨지면
# 검사가 그걸 삼켜 거짓 OK를 내면 안 된다 — CLAUDE.md "기본 태세는 fail-closed"
# 원칙을 이 서브프로세스 경계에서 실증한다.


def test_main_propagates_when_export_subprocess_fails(monkeypatch: Any, tmp_path: Any) -> None:
    """실패 주입: export_openapi.py가 비정상 종료(예: 앱 임포트 깨짐)하면
    예외가 그대로 전파돼야 한다 — 삼켜서 exit 0을 내면 안 된다."""
    baseline = _schema(
        paths={
            "/widgets": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Widget"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={"Widget": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def _fail(*args: Any, **kwargs: Any) -> None:
        raise subprocess.CalledProcessError(returncode=1, cmd=["export_openapi"])

    monkeypatch.setattr(
        cast(Any, compat_module).subprocess, "run", _fail
    )

    with pytest.raises(subprocess.CalledProcessError):
        main(["--baseline", str(baseline_path)])


def test_main_propagates_when_export_subprocess_lies_about_success(
    monkeypatch: Any, tmp_path: Any
) -> None:
    """실패 주입: 서브프로세스가 returncode 0으로 끝나도 출력 파일을 쓰지
    않으면(부분 실패·버그) 그 누락을 놓치고 통과시키면 안 된다."""
    baseline = _schema(
        paths={
            "/widgets": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Widget"}
                                }
                            }
                        }
                    }
                }
            }
        },
        schemas={"Widget": {"type": "object", "properties": {"id": {"type": "string"}}}},
    )
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

    def _noop_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(args=["export_openapi"], returncode=0)

    monkeypatch.setattr(
        cast(Any, compat_module).subprocess, "run", _noop_run
    )

    with pytest.raises(FileNotFoundError):
        main(["--baseline", str(baseline_path)])
