"""NH(Namuh Plug) OpenAPI 스펙 기계 추출 — 공식 NH Open API 문서에서 내려받는다.

BR-17(ADR-2026-09-24-A D5). 기준 목록은 공식 OpenAPI 3.0 JSON을 네트워크로
내려받아 `docs/design/nh_openapi_reference.json`으로 정본화한다. 이 파일의 스냅샷만
CI(nh_openapi_coverage.py)에서 읽어 오프라인으로 구현 상태를 분석한다.

각 경로(path) + 메서드(method) 조합마다: 경로, HTTP 메서드, 요청/응답 스키마,
요청 파라미터(필수/선택)를 추출한다.

    python scripts/nh_openapi_fetch.py

이 스크립트만 네트워크 접근을 한다(`urllib`). 스냅샷을 새로 뜨려면 위 명령을 실행하고,
생성된 JSON은 git에 커밋한다.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "design" / "nh_openapi_reference.json"

OPENAPI_URL = "https://www.nhplug.com/openapi-docs/krstock/openapi.json"
USER_AGENT = "aios-nh-openapi-coverage/1"
FETCH_TIMEOUT = 20


class NhOpenAPIFetchError(RuntimeError):
    """OpenAPI 응답이 예상 형식이 아님."""


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310
        return bytes(resp.read())


def _extract_request_body_param(operation: dict[str, Any]) -> dict[str, Any] | None:
    """requestBody 파라미터 하나를 추출한다(있으면)."""
    request_body = operation.get("requestBody")
    if not request_body or not isinstance(request_body, dict):
        return None
    content = request_body.get("content", {})
    # 대부분 application/json
    json_content = content.get("application/json", {})
    schema = json_content.get("schema")
    if not schema:
        return None
    return {
        "name": "body",
        "in": "body",
        "required": request_body.get("required", False),
        "schema": schema if isinstance(schema, dict) else str(schema),
    }


def _extract_path_query_params(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """path/query 파라미터를 추출한다."""
    params: list[dict[str, Any]] = []
    for param in operation.get("parameters", []):
        if isinstance(param, dict):
            params.append(
                {
                    "name": param.get("name"),
                    "in": param.get("in"),
                    "required": param.get("required", False),
                    "schema": param.get("schema", param.get("type")),
                }
            )
    return params


def _extract_request_params(operation: dict[str, Any]) -> list[dict[str, Any]]:
    """operation의 요청 파라미터(body + path/query)를 모두 추출한다."""
    request_params: list[dict[str, Any]] = []
    body_param = _extract_request_body_param(operation)
    if body_param is not None:
        request_params.append(body_param)
    request_params.extend(_extract_path_query_params(operation))
    return request_params


def _extract_response_schema(operation: dict[str, Any]) -> dict[str, Any] | str | None:
    """첫 번째 성공 응답(200/201/default)의 스키마를 추출한다."""
    responses = operation.get("responses", {})
    for status_code in ["200", "201", "default"]:
        response_data = responses.get(status_code)
        if not isinstance(response_data, dict):
            continue
        content = response_data.get("content", {})
        json_content = content.get("application/json", {})
        schema = json_content.get("schema")
        if schema:
            return schema if isinstance(schema, dict) else str(schema)
    return None


def _build_endpoint(path_str: str, method_str: str, operation: dict[str, Any]) -> dict[str, Any]:
    """path + method + operation 하나에서 endpoint 레코드를 만든다."""
    return {
        "path": path_str,
        "method": method_str.upper(),
        "summary": operation.get("summary", ""),
        "description": operation.get("description", ""),
        "request_params": _extract_request_params(operation),
        "response_schema": _extract_response_schema(operation),
    }


def extract_endpoints(openapi_data: dict[str, Any]) -> list[dict[str, Any]]:
    """OpenAPI spec에서 모든 endpoint를 추출한다."""
    endpoints: list[dict[str, Any]] = []
    paths = openapi_data.get("paths", {})

    for path_str, path_item in sorted(paths.items()):
        if not isinstance(path_item, dict):
            continue
        for method_str, operation in path_item.items():
            if method_str.startswith("x-"):
                continue  # skip OpenAPI extensions
            if not isinstance(operation, dict):
                continue
            endpoints.append(_build_endpoint(path_str, method_str, operation))

    return endpoints


def fetch_and_save(output: Path = DEFAULT_OUTPUT) -> int:
    """OpenAPI spec을 내려받아 스냅샷으로 저장한다."""
    try:
        print(f"Fetching {OPENAPI_URL}...")
        raw_json = _http_get(OPENAPI_URL)
        openapi_data = json.loads(raw_json)

        # Extract endpoints
        endpoints = extract_endpoints(openapi_data)

        # Build reference
        reference = {
            "source_url": OPENAPI_URL,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "spec_version": openapi_data.get("info", {}).get("version"),
            "spec_title": openapi_data.get("info", {}).get("title"),
            "endpoint_count": len(endpoints),
            "endpoints": endpoints,
        }

        # Write output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        print(
            f"OK: {len(endpoints)}개 endpoint 추출 -> {output} (fetched: {reference['fetched_at']})"
        )
        return 0

    except (NhOpenAPIFetchError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return fetch_and_save()


if __name__ == "__main__":
    raise SystemExit(main())
