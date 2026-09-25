"""RD-8 -- research_data HTTP read API integration tests (real FastAPI app +
TEST_DATABASE_URL).

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md #9 RD-8.
DoD: cross-tenant 404 (isomorphic), source-permission 403.

Same pattern as test_market_data_router.py: `source_contract` is a global
source_id-PK table shared across the whole test file, so
`get_source_contract_repository` is overridden with a `_FakeSourceContractRepository`
per test instead of seeding real rows (avoids order-dependent pollution).
`research_items`/`research_sources` rows are tenant-scoped, so those are
seeded directly per test via `PostgresResearchRepository`.
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
from src.foundation.research_data.adapters.postgres_repository import PostgresResearchRepository
from src.foundation.research_data.contracts.v1 import ResearchItem, SourceMeta
from src.main import app

STRONG_PASSWORD = "Str0ng!Passw0rd"
BASE = "/v1/foundation/research"
SOURCE_ID = "OPENDART"


def _source_contract(scope: RedistributionScope, *, source_id: str = SOURCE_ID) -> SourceContract:
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


class _FakeSourceContractRepository:
    def __init__(self, contract: SourceContract | None) -> None:
        self._contract = contract

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        return self._contract


@pytest.fixture
async def client():
    async with app.router.lifespan_context(app):
        app.dependency_overrides[get_source_contract_repository] = lambda: (
            _FakeSourceContractRepository(_source_contract(RedistributionScope.DISPLAY))
        )
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


def _item(*, source_id: str = SOURCE_ID, known_at: datetime | None = None) -> ResearchItem:
    now = known_at if known_at is not None else datetime.now(timezone.utc)
    return ResearchItem(
        item_id=uuid.uuid4(),
        source_id=source_id,
        kind="filing",
        published_at=now,
        known_at=now,
        instruments=("005930",),
        title="Test filing",
        body_ref=None,
        url="https://example.invalid/filing/1",
        language="ko",
        hash="a" * 64,
        revision_of=None,
    )


@pytest.fixture
async def seeded(client: AsyncClient) -> dict:
    headers_a, tenant_a = await _register(client)
    headers_b, _tenant_b = await _register(client)
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    await repo.upsert_source(
        SourceMeta(
            source_id=SOURCE_ID,
            publisher="Test Publisher",
            redistribution="store_full",
            license_ref="https://example.invalid/license",
            rate_limit=60,
            coverage="2020-01-01~present",
        )
    )
    item = _item()
    item_id = await repo.append_item(tenant_a, item, external_id=f"ext-{uuid.uuid4().hex}")
    return {"a": headers_a, "b": headers_b, "tenant_a": tenant_a, "item_id": item_id, "item": item}


async def test_search_items_returns_matching_items_for_source(client, seeded):
    response = await client.get(
        f"{BASE}/items", params={"source_id": SOURCE_ID}, headers=seeded["a"]
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) >= {"data", "meta"}
    assert [i["item_id"] for i in body["data"]] == [str(seeded["item_id"])]


async def test_get_item_returns_full_item(client, seeded):
    response = await client.get(f"{BASE}/items/{seeded['item_id']}", headers=seeded["a"])
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["item_id"] == str(seeded["item_id"])
    assert data["source_id"] == SOURCE_ID


async def test_cross_tenant_item_is_404_isomorphic_with_unknown_item_id(client, seeded):
    foreign = await client.get(f"{BASE}/items/{seeded['item_id']}", headers=seeded["b"])
    unknown = await client.get(f"{BASE}/items/{uuid.uuid4()}", headers=seeded["a"])
    assert foreign.status_code == unknown.status_code == 404
    foreign_body, unknown_body = foreign.json(), unknown.json()
    assert foreign_body["error_code"] == unknown_body["error_code"] == "RESOURCE_NOT_FOUND"
    assert foreign_body["message"] == unknown_body["message"]
    assert set(foreign_body) == set(unknown_body) and "data" not in foreign_body


async def test_unspecified_source_contract_denies_search(client, seeded):
    """D2 -- a missing contract row (`NOT_FOUND`) must deny, not silently pass."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(None)
    )
    response = await client.get(
        f"{BASE}/items", params={"source_id": SOURCE_ID}, headers=seeded["a"]
    )
    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "AUTHZ_FORBIDDEN"


async def test_internal_scope_source_denies_get_item(client, seeded):
    """INTERNAL redistribution scope permits only INTERNAL_CALC -- the read
    endpoint requests USER_OWN_DISPLAY, so it must deny with 403 even though
    the item itself exists and belongs to the caller's own tenant (never
    folded into 404 -- see authorize_access.py module docstring)."""
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _FakeSourceContractRepository(_source_contract(RedistributionScope.INTERNAL))
    )
    response = await client.get(f"{BASE}/items/{seeded['item_id']}", headers=seeded["a"])
    assert response.status_code == 403, response.text
    assert response.json()["error_code"] == "AUTHZ_FORBIDDEN"


async def test_unauthenticated_request_is_401_envelope(client):
    response = await client.get(f"{BASE}/items", params={"source_id": SOURCE_ID})
    assert response.status_code == 401
    assert "error_code" in response.json()


# --- D2 floor: failure-injection / numeric perf assertion / gate-red repro ---


class _BoomSourceContractRepository:
    """Failure injection -- the source-contract lookup raises (simulated DB
    outage). `authorize_source_read` must not swallow this into an allow."""

    async def get(self, conn: asyncpg.Connection, source_id: str) -> SourceContract | None:
        raise ConnectionError("simulated source_contract backend outage")


async def test_source_contract_repository_failure_fails_closed_not_open(client, seeded):
    app.dependency_overrides[get_source_contract_repository] = lambda: (
        _BoomSourceContractRepository()
    )
    try:
        response = await client.get(f"{BASE}/items/{seeded['item_id']}", headers=seeded["a"])
    finally:
        app.dependency_overrides[get_source_contract_repository] = lambda: (
            _FakeSourceContractRepository(_source_contract(RedistributionScope.DISPLAY))
        )
    assert response.status_code == 500, response.text
    body = response.json()
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "data" not in body, "장애 상황에서 item 데이터가 노출되면 안 된다(fail-closed)"
    assert "simulated source_contract backend outage" not in body["message"]


@pytest.mark.perf
async def test_search_items_latency_stays_within_normalized_ceiling(client, seeded):
    """Numeric perf assertion -- baseline-normalized ceiling (same technique
    as test_market_data_router.py) instead of an absolute threshold, since
    TEST_DATABASE_URL latency is shared/variable."""
    pool = app.state.pool
    repo = PostgresResearchRepository(pool)
    for _ in range(50):
        await repo.append_item(
            seeded["tenant_a"], _item(), external_id=f"ext-{uuid.uuid4().hex}"
        )

    baseline_start = time.perf_counter()
    baseline = await client.get(f"{BASE}/items/{seeded['item_id']}", headers=seeded["a"])
    baseline_elapsed = time.perf_counter() - baseline_start
    assert baseline.status_code == 200, baseline.text

    full_start = time.perf_counter()
    full = await client.get(
        f"{BASE}/items", params={"source_id": SOURCE_ID}, headers=seeded["a"]
    )
    full_elapsed = time.perf_counter() - full_start
    assert full.status_code == 200, full.text
    assert len(full.json()["data"]) == 51

    ceiling = baseline_elapsed * 20 + 0.5
    assert full_elapsed <= ceiling, (
        f"51개 항목 검색이 {full_elapsed:.3f}s 걸림 "
        f"(baseline {baseline_elapsed:.3f}s, 정규화 상한 {ceiling:.3f}s)"
    )


class _LeakyResearchRepository:
    """Gate-red reproduction -- simulates the adapter's `list_by_tenant` SQL
    dropping its `tenant_id` WHERE clause: it returns every tenant's items
    for the requested source, ignoring the `tenant_id` argument entirely."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._real = PostgresResearchRepository(pool)

    async def get_item(self, tenant_id: uuid.UUID, item_id: uuid.UUID) -> ResearchItem | None:
        return await self._real.get_item(tenant_id, item_id)

    async def list_by_tenant(
        self, tenant_id: uuid.UUID, *, source_id: str, limit: int = 200
    ) -> list[ResearchItem]:
        async with self._real._pool.acquire() as conn:  # noqa: SLF001 -- deliberate defect repro
            rows = await conn.fetch(
                "SELECT * FROM research_items WHERE source_id = $1 "
                "ORDER BY known_at DESC, item_id DESC LIMIT $2",
                source_id,
                limit,
            )
        from src.foundation.research_data.adapters.postgres_repository import _row_to_item

        return [_row_to_item(row) for row in rows]


async def test_search_items_gate_red_reproduction_if_repository_ignores_tenant(client, seeded):
    """Gate-red reproduction -- if the repository's tenant scoping is
    dropped, tenant B's search for the same source leaks tenant A's item.
    A 200-with-leak is the defect being reproduced, not the expected
    behaviour (contrast with test_cross_tenant_item_is_404_isomorphic...)."""
    pool = app.state.pool
    app.dependency_overrides[get_research_repository] = lambda: _LeakyResearchRepository(pool)
    try:
        leaked = await client.get(
            f"{BASE}/items", params={"source_id": SOURCE_ID}, headers=seeded["b"]
        )
    finally:
        app.dependency_overrides.pop(get_research_repository, None)

    assert leaked.status_code == 200, leaked.text
    assert str(seeded["item_id"]) in {i["item_id"] for i in leaked.json()["data"]}, (
        "list_by_tenant가 tenant_id를 무시하면 교차 테넌트 검색 방어는 무력화된다"
    )
