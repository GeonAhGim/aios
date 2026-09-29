"""RD-17(task-7775) -- /v1/foundation/research-data/search·/sources 통합
테스트 (real FastAPI app + TEST_DATABASE_URL).

RD-8(test_research_data_router.py)과 같은 픽스처 관용(전역 source_contract는
`get_source_contract_repository` override로 페이크, research_items/
research_sources는 `PostgresResearchRepository`로 직접 시딩)을 재사용하되,
이 라우터는 `source_id`를 요구하지 않는 다중 소스 횡단 검색이라 소스별로
다른 계약(허용/거부)을 돌려주는 `_PerSourceContractRepository`를 쓴다.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.routers.research_data import get_research_repository, get_source_contract_repository
from src.foundation.market_data.domain.entitlement.source_contract import (
    RedistributionScope,
    SourceCapability,
    SourceContract,
    SourceContractTier,
)
from src.foundation.research_data.adapters.postgres_repository import (
    PostgresResearchRepository,
    _row_to_item,
)
from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/research-data"
SOURCE_A = "OPENDART"
SOURCE_B = "GDELT"


def _contract(scope: RedistributionScope, *, source_id: str) -> SourceContract:
    now = datetime.now(timezone.utc)
    return SourceContract(
        source_id=source_id,
        tier=SourceContractTier.ENTERPRISE,
        credential_ref="test:none",
        redistribution_scope=scope,
        rate_limit=1000,
        quota=1_000_000,
        valid_from=now - timedelta(days=365),
        valid_to=None,
        capability=SourceCapability(
            asset_classes=frozenset({"EQUITY"}), resolutions=frozenset({"1d"})
        ),
    )


class _PerSourceContractRepository:
    def __init__(self, contracts: dict[str, SourceContract | None]) -> None:
        self._contracts = contracts

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return self._contracts.get(source_id)


class _AllAllowedContractRepository:
    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return _contract(RedistributionScope.DISPLAY, source_id=source_id)


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_source_contract_repository] = _AllAllowedContractRepository
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
        app.dependency_overrides.pop(get_source_contract_repository, None)


async def _register(client: AsyncClient) -> tuple[dict, uuid.UUID]:
    response = await client.post(
        "/auth/register",
        json={"email": f"test-{uuid.uuid4().hex}@example.com", "password": STRONG_PASSWORD},
    )
    token = response.json()["data"]["access_token"]
    user_id = jwt.decode(token, options={"verify_signature": False})["sub"]
    return {"Authorization": f"Bearer {token}"}, uuid.UUID(user_id)


def _item(
    *, source_id: str, title: str = "Test filing", known_at: datetime | None = None
) -> ResearchItem:
    now = known_at if known_at is not None else datetime.now(timezone.utc)
    return ResearchItem(
        item_id=uuid.uuid4(),
        source_id=source_id,
        kind="filing",
        published_at=now,
        known_at=now,
        instruments=("005930",),
        title=title,
        body_ref=None,
        url="https://example.invalid/filing/1",
        language="ko",
        hash="a" * 64,
        revision_of=None,
    )


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    headers_b, tenant_b = await _register(client)
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    for source_id in (SOURCE_A, SOURCE_B):
        await repo.upsert_source(
            SourceMeta(
                source_id=source_id,
                publisher=f"Publisher {source_id}",
                redistribution="store_full",
                license_ref="https://example.invalid/license",
                rate_limit=60,
                coverage="2020-01-01~present",
            )
        )
    item_a = _item(source_id=SOURCE_A, title="삼성전자 분기보고서")
    item_a_id = await repo.append_item(tenant_a, item_a, external_id=f"ext-{uuid.uuid4().hex}")
    item_b = _item(source_id=SOURCE_B, title="글로벌 뉴스 속보")
    item_b_id = await repo.append_item(tenant_a, item_b, external_id=f"ext-{uuid.uuid4().hex}")
    return {
        "a": headers_a,
        "b": headers_b,
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "item_a_id": item_a_id,
        "item_b_id": item_b_id,
    }


async def test_search_returns_matching_items_across_sources(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert {i["item_id"] for i in body["items"]} == {
        str(seeded["item_a_id"]),
        str(seeded["item_b_id"]),
    }
    assert body["total"] == 2
    assert body["truncated"] is False


async def test_search_filters_by_title_query(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "삼성전자", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [i["item_id"] for i in body["items"]] == [str(seeded["item_a_id"])]


# --- negative ---


async def test_negative_1_empty_result_is_200_with_empty_list(client, seeded):
    """DoD -- 빈 결과는 500/예외가 아니라 200 + 빈 리스트다."""
    response = await client.post(
        f"{BASE}/search", json={"query": "no-such-title-xyz", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body == {"items": [], "total": 0, "truncated": False}


async def test_negative_2_unauthorized_source_items_are_silently_excluded(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = lambda: _PerSourceContractRepository(
        {
            SOURCE_A: _contract(RedistributionScope.DISPLAY, source_id=SOURCE_A),
            SOURCE_B: None,  # no contract row -> denied
        }
    )
    response = await client.post(
        f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert [i["item_id"] for i in body["items"]] == [str(seeded["item_a_id"])]


async def test_negative_3_cross_tenant_search_does_not_leak_items(client, seeded):
    response = await client.post(
        f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["b"]
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["items"] == []


async def test_negative_4_unauthenticated_search_is_401_envelope(client):
    response = await client.post(f"{BASE}/search", json={"query": "", "kinds": []})
    assert response.status_code == 401
    assert "error_code" in response.json()


async def test_negative_5_sources_endpoint_never_500s_on_a_bare_request(client):
    """`research_sources`는 테넌트 스코프가 없는 공유 카탈로그라(다른 테스트가
    남긴 행이 섞일 수 있다) 빈 리스트 자체를 단언하지 않지만, 인증 없이도
    500/예외 없이 200 + 리스트 응답이어야 한다."""
    response = await client.get(f"{BASE}/sources")
    assert response.status_code == 200, response.text
    assert isinstance(response.json()["data"], list)


async def test_sources_endpoint_returns_catalog(client, seeded):
    response = await client.get(f"{BASE}/sources")
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert {SOURCE_A, SOURCE_B} <= {s["source_id"] for s in body}


# --- D2 floor: failure-injection / numeric perf assertion / gate-red repro ---


class _BoomSourceContractRepository:
    """Failure injection -- the source-contract lookup raises (simulated DB
    outage) for every source. `authorize_source_read` must not swallow this
    into a silent exclusion -- an unexpected error still fails closed as a
    500, distinct from the ordinary "no contract row" case which excludes
    just that one source."""

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        raise ConnectionError("simulated source_contract backend outage")


async def test_search_source_contract_failure_fails_closed_not_open(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = _BoomSourceContractRepository
    try:
        response = await client.post(
            f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"]
        )
    finally:
        app.dependency_overrides[get_source_contract_repository] = _AllAllowedContractRepository
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "data" not in body, "장애 상황에서 검색 결과가 노출되면 안 된다(fail-closed)"
    assert "simulated source_contract backend outage" not in body["message"]


@pytest.mark.perf
async def test_search_latency_stays_within_normalized_ceiling(client, seeded):
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    for _ in range(50):
        await repo.append_item(
            seeded["tenant_a"], _item(source_id=SOURCE_A), external_id=f"ext-{uuid.uuid4().hex}"
        )

    baseline_start = time.perf_counter()
    baseline = await client.get(f"{BASE}/sources")
    baseline_elapsed = time.perf_counter() - baseline_start
    assert baseline.status_code == 200, baseline.text

    full_start = time.perf_counter()
    full = await client.post(f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["a"])
    full_elapsed = time.perf_counter() - full_start
    assert full.status_code == 200, full.text
    assert len(full.json()["data"]["items"]) == 52

    ceiling = baseline_elapsed * 20 + 0.5
    assert full_elapsed <= ceiling, (
        f"52개 항목 검색이 {full_elapsed:.3f}s 걸림 "
        f"(baseline {baseline_elapsed:.3f}s, 정규화 상한 {ceiling:.3f}s)"
    )


class _LeakyResearchRepository:
    """Gate-red reproduction -- `list_by_tenant_across_sources`가 tenant_id
    WHERE 절을 빠뜨리면 어떻게 되는지 재현한다: 요청한 테넌트가 아니라 전체
    테넌트의 항목을 돌려준다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._real = PostgresResearchRepository(pool)

    async def list_by_tenant_across_sources(
        self, tenant_id: uuid.UUID, *, limit: int = 200
    ) -> list[ResearchItem]:
        async with self._real._pool.acquire() as conn:  # noqa: SLF001 -- deliberate defect repro
            rows = await conn.fetch(
                "SELECT * FROM research_items ORDER BY known_at DESC, item_id DESC LIMIT $1",
                limit,
            )
        return [_row_to_item(row) for row in rows]

    async def get_linked_instrument_ids(self, item_ids):
        return await self._real.get_linked_instrument_ids(item_ids)


async def test_search_gate_red_reproduction_if_repository_ignores_tenant(client, seeded):
    """tenant_id 필터가 빠지면 tenant B의 검색이 tenant A의 항목을 그대로
    본다 -- 200-with-leak이 재현 대상 결함이지 기대 동작이 아니다."""
    pool = app.state.pool
    app.dependency_overrides[get_research_repository] = lambda: _LeakyResearchRepository(pool)
    try:
        leaked = await client.post(
            f"{BASE}/search", json={"query": "", "kinds": []}, headers=seeded["b"]
        )
    finally:
        app.dependency_overrides.pop(get_research_repository, None)

    assert leaked.status_code == 200, leaked.text
    leaked_ids = {i["item_id"] for i in leaked.json()["data"]["items"]}
    assert str(seeded["item_a_id"]) in leaked_ids, (
        "list_by_tenant_across_sources가 tenant_id를 무시하면 교차 테넌트 검색 방어는 무력화된다"
    )
