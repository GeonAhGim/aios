"""IND-12 — `GET /v1/indicators` 통합테스트(실제 FastAPI 앱 + TEST_DATABASE_URL).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.9 IND-12.
DoD: 3층 이름 충돌 결정론적 해소·버전/해시 노출·검색/카테고리/커서 페이지네이션.
negative: 미인증 401, 교차 테넌트 스크립트 지표 미노출, 잘못된 커서 400.

INVARIANTS I-10(배선 증명): 라우트가 실제 앱에 마운트돼 있고, 인증 없이는
거부되며, 커서 오류가 전역 핸들러 봉투로 나오는지 앱 왕복으로 단언한다.
"""
from __future__ import annotations

import ast
import uuid
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.indicators import get_script_indicator_source
from src.core.indicators.catalog.registry_tiers import DEFAULT_STATIC_CATALOG, ScriptIndicatorEntry
from src.core.indicators.spec import IndicatorSpec, PlotSpec
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
PATH = "/v1/indicators"


def _fake_script_spec(name: str) -> IndicatorSpec:
    return IndicatorSpec(
        name=name,
        inputs=("close",),
        params=(),
        outputs=("value",),
        lookback=lambda params: 0,
        plots=(PlotSpec(kind="line", scale="own", default_pane="separate"),),
    )


class _FakeScriptSource:
    def __init__(self, entries: list[ScriptIndicatorEntry]) -> None:
        self._entries = entries

    def list_for_tenant(self, tenant_id: UUID) -> list[ScriptIndicatorEntry]:
        return self._entries


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_script_indicator_source, None)


async def _register(client: AsyncClient) -> tuple[dict[str, str], UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    me = await client.get("/users/me", headers=headers)
    return headers, UUID(me.json()["data"]["user_id"])


# ---- I-10 배선 증명 ----


def test_route_is_mounted_on_app() -> None:
    paths = app.openapi()["paths"]
    assert PATH in paths
    assert set(paths[PATH]) == {"get"}


def test_router_has_zero_raw_http_exception() -> None:
    source = (
        Path(__file__).resolve().parents[3] / "src/api/routers/indicators.py"
    ).read_text("utf-8")
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


async def test_requires_auth(client: AsyncClient) -> None:
    response = await client.get(PATH)
    assert response.status_code == 401
    body = response.json()
    assert body["error_code"].startswith("AUTH_")
    assert "trace_id" in body


# ---- 목록/봉투/버전·해시 ----


async def test_list_success_envelope_exposes_version_and_hash(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    response = await client.get(PATH, params={"q": "SMA", "limit": 10}, headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"data", "meta"}
    assert body["meta"]["page"]["size"] == 10
    items = body["data"]["items"]
    assert any(item["name"] == "SMA" for item in items)
    sma = next(item for item in items if item["name"] == "SMA")
    assert sma["tier"] == "core"
    assert len(sma["hash"]) == 64
    assert sma["version"] == "ind-v1"
    assert sma["inputs"] and sma["outputs"]


async def test_search_is_case_insensitive_substring(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    response = await client.get(PATH, params={"q": "sma", "limit": 50}, headers=headers)
    names = {item["name"] for item in response.json()["data"]["items"]}
    assert "SMA" in names


async def test_category_filter_matches_core_talib_group(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    category = DEFAULT_STATIC_CATALOG["SMA"].category
    response = await client.get(
        PATH, params={"category": category, "limit": 200}, headers=headers
    )
    items = response.json()["data"]["items"]
    assert items
    assert all(item["category"] == category for item in items)
    assert any(item["name"] == "SMA" for item in items)


async def test_cursor_pagination_walks_full_catalog_without_duplicates(
    client: AsyncClient,
) -> None:
    headers, _ = await _register(client)
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(50):  # 안전 상한 — 161종/limit=20이면 9페이지면 끝난다
        params = {"limit": 20}
        if cursor is not None:
            params["cursor"] = cursor
        response = await client.get(PATH, params=params, headers=headers)
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(item["name"] for item in body["data"]["items"])
        cursor = body["meta"]["page"]["next_cursor"]
        if cursor is None:
            break
    else:
        pytest.fail("페이지네이션이 끝나지 않음")
    assert len(seen) == len(set(seen)) == len(DEFAULT_STATIC_CATALOG)
    assert sorted(seen) == seen  # 이름 오름차순


# ---- negative: 잘못된 커서 400 ----


async def test_invalid_cursor_is_400(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    response = await client.get(PATH, params={"cursor": "bad cursor!!"}, headers=headers)
    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert "trace_id" in body


# ---- negative: 교차 테넌트 스크립트 지표 미노출 ----


async def test_script_indicator_visible_to_owning_tenant(client: AsyncClient) -> None:
    headers, tenant_id = await _register(client)
    name = f"MY_SCRIPT_{uuid.uuid4().hex[:8].upper()}"
    entry = ScriptIndicatorEntry(
        name=name,
        tenant_id=tenant_id,
        spec=_fake_script_spec(name),
        script_hash="a" * 64,
    )
    app.dependency_overrides[get_script_indicator_source] = lambda: _FakeScriptSource([entry])
    response = await client.get(PATH, params={"q": name}, headers=headers)
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["tier"] == "script"
    assert items[0]["version"] == "a" * 64
    assert items[0]["hash"] == "a" * 64


async def test_script_indicator_hidden_from_other_tenant(client: AsyncClient) -> None:
    headers, _own_tenant_id = await _register(client)
    other_tenant_id = uuid.uuid4()
    name = f"OTHER_SCRIPT_{uuid.uuid4().hex[:8].upper()}"
    entry = ScriptIndicatorEntry(
        name=name,
        tenant_id=other_tenant_id,
        spec=_fake_script_spec(name),
        script_hash="b" * 64,
    )
    app.dependency_overrides[get_script_indicator_source] = lambda: _FakeScriptSource([entry])
    response = await client.get(PATH, params={"q": name}, headers=headers)
    assert response.json()["data"]["items"] == []


async def test_script_indicator_yields_to_core_name_conflict(client: AsyncClient) -> None:
    """3층 우선순위 — SCRIPT가 CORE와 같은 이름을 자처해도 CORE가 이긴다."""
    headers, tenant_id = await _register(client)
    entry = ScriptIndicatorEntry(
        name="SMA", tenant_id=tenant_id, spec=_fake_script_spec("SMA"), script_hash="c" * 64
    )
    app.dependency_overrides[get_script_indicator_source] = lambda: _FakeScriptSource([entry])
    response = await client.get(PATH, params={"q": "SMA", "limit": 50}, headers=headers)
    items = [item for item in response.json()["data"]["items"] if item["name"] == "SMA"]
    assert len(items) == 1
    assert items[0]["tier"] == "core"
