"""DSL-12 — `POST /v1/scripts/compile` 통합테스트(실제 FastAPI 앱 + TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.4 DSL-12.
DoD: 4종 오류 코드(details.code)·오류 위치(details.line/col)·ApiResponse 봉투·
EXCEPTION_MAP 경유(raw HTTPException 0건)·script_hash 결정론.

INVARIANTS I-10(배선 증명): 라우트가 실제 앱에 마운트돼 있고, 인증 없이는
거부되며, 컴파일 오류가 전역 핸들러 봉투로 나오는지 앱 왕복으로 단언한다.
성능은 응답의 `elapsed_ms`를 print만 한다(지연 단언 금지).
"""
from __future__ import annotations

import ast
import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.schemas.scripts import MAX_SOURCE_CHARS
from src.core.indicators.registry import DEFAULT_REGISTRY
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
PATH = "/v1/scripts/compile"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAMPLE = (
    "input length: int = 14\n"
    "input close: series<float> = 0\n"
    "let rsi_val = ta.rsi(close, length)\n"
    "signal go_long = rsi_val < 30\n"
    "plot(rsi_val, 1)\n"
)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def _auth(client: AsyncClient) -> dict[str, str]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


# ---- I-10 배선 증명 ----


def test_route_is_mounted_on_app() -> None:
    """FastAPI 0.141+는 include_router를 지연 래퍼(`_IncludedRouter`)로 담아
    `app.routes`에 경로가 직접 보이지 않는다 — 실효 경로표인 OpenAPI 스키마로 증명한다."""
    paths = app.openapi()["paths"]
    assert PATH in paths
    assert "post" in paths[PATH]
    assert set(paths[PATH]) == {"post"}


def test_router_has_zero_raw_http_exception() -> None:
    source = (Path(__file__).resolve().parents[3] / "src/api/routers/scripts.py").read_text("utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


async def test_requires_auth(client: AsyncClient) -> None:
    response = await client.post(PATH, json={"source": SAMPLE})
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"].startswith("AUTH_")
    assert "trace_id" in body


# ---- 성공 봉투 ----


async def test_compile_success_envelope(client: AsyncClient) -> None:
    headers = await _auth(client)
    response = await client.post(PATH, json={"source": SAMPLE}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    assert "trace_id" in body["meta"] and "as_of" in body["meta"]
    data = body["data"]
    assert _HEX64.match(data["script_hash"])
    assert data["registry_version"] == DEFAULT_REGISTRY.registry_hash()
    assert data["grammar_version"] == "aios-script-1"
    assert data["ir_version"] == "aios-ir-1"
    assert _HEX64.match(data["ir_sha256"])
    assert data["instr_count"] > 0
    assert data["resources"]["plot_count"] == 1
    print(f"[DSL-12] POST {PATH} elapsed_ms={data['elapsed_ms']}")


async def test_compile_is_deterministic_across_requests(client: AsyncClient) -> None:
    headers = await _auth(client)
    first = await client.post(PATH, json={"source": SAMPLE}, headers=headers)
    second = await client.post(PATH, json={"source": SAMPLE}, headers=headers)
    assert first.json()["data"]["script_hash"] == second.json()["data"]["script_hash"]


# ---- 4종 오류 코드 + 위치(negative) ----


@pytest.mark.parametrize(
    ("source", "code", "line", "col"),
    [
        ("let a = 1 +", "SCRIPT_SYNTAX", 1, 12),
        ("input a: int = 1\nlet bad = zz", "SCRIPT_TYPE", 2, 1),
        ("input c: series<float> = 0\nlet a = ta.security(c, 1)", "SCRIPT_LOOKAHEAD", 2, 12),
        ("input c: series<float> = 0\n" + "plot(c)\n" * 33, "SCRIPT_RESOURCE_LIMIT", 34, 1),
    ],
)
async def test_compile_errors_are_400_with_code_and_position(
    client: AsyncClient, source: str, code: str, line: int, col: int
) -> None:
    headers = await _auth(client)
    response = await client.post(PATH, json={"source": source}, headers=headers)
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"] == {"code": code, "line": line, "col": col}
    assert code in body["message"]
    assert "trace_id" in body


async def test_oversize_source_rejected_by_transport_validation(client: AsyncClient) -> None:
    headers = await _auth(client)
    response = await client.post(
        PATH, json={"source": "x" * (MAX_SOURCE_CHARS + 1)}, headers=headers
    )
    assert response.status_code == 400
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "body.source" in body["details"]["fields"]
