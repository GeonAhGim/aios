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
            # operation 객체 구조: summary, description, requestBody, responses, parameters
            method_upper = method_str.upper()

            # requestBody 파라미터 추출
            request_params: list[dict[str, Any]] = []
            request_body = operation.get("requestBody")
            if request_body and isinstance(request_body, dict):
                required = request_body.get("required", False)
                content = request_body.get("content", {})
                # 대부분 application/json
                json_content = content.get("application/json", {})
                schema = json_content.get("schema")
                if schema:
                    request_params.append(
                        {
                            "name": "body",
                            "in": "body",
                            "required": required,
                            "schema": schema if isinstance(schema, dict) else str(schema),
                        }
                    )

            # path/query 파라미터 추출
            for param in operation.get("parameters", []):
                if isinstance(param, dict):
                    request_params.append(
                        {
                            "name": param.get("name"),
                            "in": param.get("in"),
                            "required": param.get("required", False),
                            "schema": param.get("schema", param.get("type")),
                        }
                    )

            # 응답 스키마 추출 (첫 번째 성공 응답)
            response_schema = None
            responses = operation.get("responses", {})
            for status_code in ["200", "201", "default"]:
                if status_code in responses:
                    response_data = responses[status_code]
                    if isinstance(response_data, dict):
                        content = response_data.get("content", {})
                        json_content = content.get("application/json", {})
                        schema = json_content.get("schema")
                        if schema:
                            response_schema = schema if isinstance(schema, dict) else str(schema)
                            break

            endpoint = {
                "path": path_str,
                "method": method_upper,
                "summary": operation.get("summary", ""),
                "description": operation.get("description", ""),
                "request_params": request_params,
                "response_schema": response_schema,
            }
            endpoints.append(endpoint)

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
            f"OK: {len(endpoints)}개 endpoint 추출 -> {output} "
            f"(fetched: {reference['fetched_at']})"
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
