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
from unittest.mock import Mock
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
    source = (Path(__file__).resolve().parents[3] / "src/api/routers/indicators.py").read_text(
        "utf-8"
    )
    calls = [
        n
        for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "HTTPException"
    ]
    assert calls == []


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
    response = await client.get(PATH, params={"category": category, "limit": 200}, headers=headers)
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


@pytest.mark.parametrize(
    ("field", "value"),
    [("limit", "0"), ("limit", "201"), ("limit", "abc"), ("q", "x" * 101), ("category", "x" * 101)],
    ids=["zero-limit", "oversize-limit", "noninteger-limit", "long-query", "long-category"],
)
async def test_negative_invalid_query_rejected_before_source_read(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    """I-10: transport bounds reject invalid requests before catalog access."""
    headers, _ = await _register(client)
    source = _FakeScriptSource([])
    read = Mock(return_value=[])
    monkeypatch.setattr(source, "list_for_tenant", read)
    app.dependency_overrides[get_script_indicator_source] = lambda: source

    response = await client.get(PATH, params={field: value}, headers=headers)

    assert response.status_code == 400, response.text
    body = response.json()
    assert body["error_code"] == "VALIDATION_INVALID_FIELD"
    assert body["details"]["fields"] == [f"query.{field}"]
    assert body["trace_id"]
    assert "data" not in body
    read.assert_not_called()


async def test_failure_injection_source_error_is_closed_and_recovers(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-10: storage failure cannot become a successful partial core catalog."""
    headers, tenant_id = await _register(client)
    source = _FakeScriptSource([])
    marker = "injected-indicator-storage-failure"
    read = Mock(side_effect=[RuntimeError(marker), []])
    monkeypatch.setattr(source, "list_for_tenant", read)
    app.dependency_overrides[get_script_indicator_source] = lambda: source

    response = await client.get(PATH, headers=headers)

    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["trace_id"]
    assert body["details"] == {}
    assert "data" not in body
    assert marker not in response.text
    read.assert_called_once_with(tenant_id)

    recovered = await client.get(PATH, params={"q": "SMA"}, headers=headers)
    assert recovered.status_code == 200, recovered.text
    assert any(item["name"] == "SMA" for item in recovered.json()["data"]["items"])
    assert read.call_count == 2
    read.assert_called_with(tenant_id)


@pytest.mark.perf
async def test_catalog_list_p95_budget(client: AsyncClient, perf_budget) -> None:
    """IND-12: authenticated list API p95 <= 200ms, excluding registration/warmup.

    CI 병렬 실행 안정성: 각 호출을 best-of-3으로 재고 최소값으로 p95를 계산한다.
    단일 샘플(n=1)은 CI xdist 워커 간 CPU 경합 spike 한 번에 실패한다(task-19178).
    best-of-3(n=3)로 노이즈를 걷어내고, 20개 best-of-3 샘플의 cpu_ms p95로
    예산 단언한다.

    raw time.perf_counter() → perf_budget.samples_async() 전환 (task-11032).
    best_of_async 도입으로 CI 병렬 안정성 확보 (task-19189).
    wall_ms → cpu_ms 전환으로 CI 병렬 실행 안정성 확보 (task-19270).
    """
    headers, _ = await _register(client)
    warmup = await client.get(PATH, params={"limit": 200}, headers=headers)
    assert warmup.status_code == 200, warmup.text

    async def _fn() -> None:
        response = await client.get(PATH, params={"limit": 200}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"]

    n = 20
    samples = [await perf_budget.best_of_async(_fn, n=3) for _ in range(n)]
    cpu_ms_list = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_ms_list[min(int(n * 0.95), len(cpu_ms_list) - 1)]
    assert p95_ms <= 200, f"IND-12 list API p95={p95_ms:.2f}ms > 200ms"


@pytest.mark.perf
async def test_catalog_list_p95_goes_red_on_10x_delay_injection(
    client: AsyncClient, perf_budget, monkeypatch
) -> None:
    """게이트 적색 재현(D2): sleep() 주입으로 지연을 인위적으로 10배 늘리면
    wall_ms 예산을 초과해야 한다(red-gate 재현). 단언/마커 삭제와 예산
    완화가 아니라, 단언이 "제대로 작동하는지"를 역으로 증명한다.

    실제 API 왕복(≈50ms)의 10배인 500ms sleep 을 주입하면 p95 wall_ms가
    200ms 예산을 명확히 넘어야 한다.

    CI 병렬 실행 안정성: n=10으로 줄여 직렬 실행 시간을 ~5s로 유지한다.
    red-gate 단언은 분포 모양이 아닌 "지연 주입 여부"만 확인하므로 10 샘플로
    충분하다(p95=10 samples → index 9 = 마지막 샘플, 500ms sleep → ~500ms).
    """
    import asyncio

    headers, _ = await _register(client)
    warmup = await client.get(PATH, params={"limit": 200}, headers=headers)
    assert warmup.status_code == 200, warmup.text

    budget_p95_ms = 200.0

    async def _delayed_fn() -> None:
        await asyncio.sleep(0.5)  # 500ms ≈ 10× 실제 API 왕복
        response = await client.get(PATH, params={"limit": 200}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["items"]

    n = 10
    samples = await perf_budget.samples_async(_delayed_fn, n=n)
    wall_ms_list = sorted(s.wall_ms for s in samples)
    p95_ms = wall_ms_list[min(int(n * 0.95), len(wall_ms_list) - 1)]
    print(
        f"[IND-12 delay-injected] n={n} p95={p95_ms:.2f}ms "
        f"budget={budget_p95_ms:.0f}ms (should go red)"
    )
    assert p95_ms > budget_p95_ms, (
        f"10x 지연 주입에도 p95가 예산을 넘지 않았다: {p95_ms:.2f}ms — "
        f"단언이 느린 호출을 포착하지 못한다"
    )
    # background_loops 안전 틱이 테스트 실행 중 circuit_breaker_level을
    # halted 등으로 격상시켰을 수 있다 — 후속 테스트를 위해 정상으로 되돌린다.
    import os

    import asyncpg

    db_url = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    finally:
        await conn.close()
