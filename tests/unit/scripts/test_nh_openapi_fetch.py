"""nh_openapi_fetch.extract_endpoints() 스모크 테스트 -- task-6792.

BR-17 스펙 추출기가 CI complexity 게이트(CAP=25) 아래로 분리된 뒤에도
path/method/파라미터/응답 스키마 추출 동작이 그대로인지 확인한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from nh_openapi_fetch import extract_endpoints  # noqa: E402


def test_extract_endpoints_full_operation() -> None:
    spec = {
        "paths": {
            "/orders": {
                "post": {
                    "summary": "주문 생성",
                    "description": "신규 주문을 생성한다",
                    "requestBody": {
                        "required": True,
                        "content": {"application/json": {"schema": {"type": "object"}}},
                    },
                    "parameters": [
                        {
                            "name": "account_no",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {
                        "200": {
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            }
        }
    }

    endpoints = extract_endpoints(spec)

    assert len(endpoints) == 1
    endpoint = endpoints[0]
    assert endpoint["path"] == "/orders"
    assert endpoint["method"] == "POST"
    assert endpoint["summary"] == "주문 생성"
    assert endpoint["request_params"] == [
        {"name": "body", "in": "body", "required": True, "schema": {"type": "object"}},
        {"name": "account_no", "in": "query", "required": True, "schema": {"type": "string"}},
    ]
    assert endpoint["response_schema"] == {"type": "object"}


def test_extract_endpoints_skips_openapi_extensions() -> None:
    spec = {"paths": {"/orders": {"x-internal": {"note": "not an operation"}}}}

    assert extract_endpoints(spec) == []


def test_extract_endpoints_skips_non_dict_path_item() -> None:
    spec = {"paths": {"/orders": "not-a-dict"}}

    assert extract_endpoints(spec) == []


def test_extract_endpoints_skips_non_dict_operation() -> None:
    spec = {"paths": {"/orders": {"get": "not-a-dict"}}}

    assert extract_endpoints(spec) == []


def test_extract_endpoints_without_request_body_or_responses() -> None:
    spec = {"paths": {"/health": {"get": {}}}}

    endpoints = extract_endpoints(spec)

    assert len(endpoints) == 1
    assert endpoints[0]["request_params"] == []
    assert endpoints[0]["response_schema"] is None


def test_extract_endpoints_empty_paths() -> None:
    assert extract_endpoints({"paths": {}}) == []
